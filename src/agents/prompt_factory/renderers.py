# ================================
# src/agents/prompt_factory/renderers.py
#
# Actor prompt dynamic section renderers.
# Markdown prompt files are tagless; renderers wrap them at assembly time.
#
# Classes
#   - _SafeFormatDict : str.format_map용 dict — 미등록 플레이스홀더를 원형 그대로 보존
#
# Functions
#   - _read_optional_prompt(relative_path: str) -> str : prompt/ 하위 Markdown 파일 읽기
#   - _render_prompt_block(tag: str, body: str) -> str : 본문을 XML 블록으로 감싸기
#   - render_current_pov(pov_mode: str, impersonation_allowed: bool, current_pov: dict | None) -> str : 현재 POV 동적 블록 렌더링
#   - render_reproductive_state(char_data: dict, npcs: list[dict]) -> str : 등장 여성 캐릭터 생식 상태 렌더링
#   - render_state_line(dyn_state: dict, world_config: dict | None) -> str : 상태 한 줄 렌더링
#   - clean_prompt_dict(data: dict) -> dict : 내부 키·null 값 제거
#   - join_rendered_context(rendered_context: dict[str, str]) -> str : 동적 컨텍스트 블록 결합
#   - render_active_characters_section(char_data: dict, user_data: dict, npcs: list[dict], scene_types: list[str]) -> str : 현재 등장 캐릭터 프로필 렌더링 (전체 필드)
#   - render_header(location: str, dt: datetime) -> str : 날짜·시간·장소 헤더 렌더링
# ================================

from datetime import datetime
from pathlib import Path

from src.simulation.systems.world_dynamics.organic_models import (
    normalize_contraception_value,
)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_PROMPT_HIDDEN_STATE_KEYS: frozenset[str] = frozenset({
    "contraception",
    "ts_acceptance",
})


_DEFAULT_STATE_FIELDS: list[tuple[str, str, frozenset]] = [
    ("mood", "mood", frozenset()),
    ("physical_condition", "physical", frozenset()),
    ("mental_condition", "mental", frozenset()),
    ("stress_level", "stress", frozenset({None})),
    ("outfit", "outfit", frozenset({"", None})),
    ("injury_marks", "injury", frozenset({"없음", "", None})),
]

# ----------------
# Prompt file helpers
# ----------------

def _read_optional_prompt(relative_path: str) -> str:
    """Read a tagless Markdown prompt file from prompt_factory/prompt/."""
    path = PROMPT_DIR / relative_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


class _SafeFormatDict(dict):
    """str.format_map용 dict — 미등록 플레이스홀더를 원형 그대로 보존한다.

    Preserved examples: {state_line}, {current_pov_line}, {for_add}
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_prompt_block(tag: str, body: str) -> str:
    """Wrap optional prompt text in a named block.

    The tag parameter may include attributes, e.g. 'scene type="intimate"'.
    Closing tag uses only the tag name (before the first space).
    """
    if not body:
        return ""
    return f"<{tag}>\n{body.strip()}\n</{tag.split()[0]}>"


# ----------------
# Current POV
# ----------------

def render_current_pov(
    pov_mode: str,
    impersonation_allowed: bool,
    current_pov: dict | None = None,
) -> str:
    """Render the turn's narrator anchor and player-ownership scope.

    Replaces the POV / USER CONTROL / PRE-DRAFT checklist slots: the same facts now
    reach the Actor as a dynamic state block instead of a self-report template.
    {char} and {user} stay as placeholders for the caller's variable substitution.
    """
    if pov_mode.startswith("1p_"):
        pov_line = (
            "POV: 1P | narrator={char} | access={char} perception/body/thought/dialogue/action "
            "| blocked={user}/NPC hidden state"
        )
        first_beat = (
            "{char} perception/action first; {user} action within allowed scope"
            if impersonation_allowed
            else "{char} perception/action only; no {user} action/speech/feeling"
        )
    else:
        anchor = "{user}" if pov_mode.endswith("_user") else "{char}"
        pov_line = (
            f"POV: 3P | anchor={anchor} | access=anchor perception/observable behavior "
            "| blocked=non-anchor hidden state"
        )
        first_beat = (
            "anchor perception/observable first; {user} action within allowed scope"
            if impersonation_allowed
            else "anchor perception/observable only; no {user} action/speech/feeling"
        )

    if impersonation_allowed:
        user_control = "USER CONTROL: {user} action may be narrated within the granted scope; keep its scale proportional to the player input."
    else:
        user_control = "USER CONTROL: never narrate {user} action, speech, inner state, sensation, or decision."

    lines = [
        pov_line,
        user_control,
        f"FIRST BEAT: {first_beat}",
        f"NARRATOR: {_render_current_pov_line(current_pov or {})}",
    ]
    return _render_prompt_block("current_pov", "\n".join(lines))


def _render_current_pov_line(current_pov: dict) -> str:
    """Render the selected POV candidate as one compact phrase."""
    selected = current_pov.get("selected") or {}
    if not selected:
        return "unknown -> keep narrator as current primary character."
    name = str(selected.get("name") or selected.get("id") or "unknown").strip()
    if selected.get("hide_metadata") is True:
        return name
    char_id = str(selected.get("id") or "").strip()
    source = str(selected.get("source") or "unknown").strip()
    label = f"{name}({char_id})" if char_id and char_id != name else name
    facts = _compact_pov_facts(selected)
    facts_part = f" | canon={facts}" if facts else ""
    return f"{label} | source={source} | access=current_pov only{facts_part}"


def _compact_pov_facts(selected: dict) -> str:
    """Return stable current-POV canon facts that prevent identity hallucination."""
    profile = selected.get("profile") or {}
    dynamic_state = selected.get("dynamic_state") or {}
    fields = [
        ("age", profile.get("age")),
        ("gender", profile.get("gender")),
        ("role", profile.get("role")),
        ("status", profile.get("current_status") or dynamic_state.get("current_status")),
        ("location", dynamic_state.get("location_id")),
    ]
    parts = [f"{key}={value}" for key, value in fields if value not in (None, "")]
    return "; ".join(parts)


# ----------------
# Reproductive state
# ----------------

# 임신 단계 묘사 가이드.
#   - DB의 pregnancy_day는 일(day) 단위이므로 의학적 "주(week)"를 일 범위로 변환했다
#     (기존 코드 관례와 동일: 일수 = 주수 × 7, 주 N = (N-1)*7+1 ~ N*7일).
#   - 정확한 일수/주수는 프롬프트에 노출하지 않는다(모델이 "53일째/N주차"로 받아쓰는 것을
#     막기 위함). 대신 현재 단계의 몸 상태 + 묘사 포인트만 정성적으로 흘린다.
#   - (max_day, 단계, 몸 상태, 묘사 포인트) — preg_day <= max_day인 첫 항목을 채택.
_PREGNANCY_STAGES: list[tuple[int, str, str, str]] = [
    (28,  "very early",
          "Often unaware herself. Missed period, faint fatigue. No visible change.",
          "Keep it subtle: \"body feels oddly heavy\", \"the smell of coffee is off-putting\"."),
    (56,  "early",
          "Pregnancy noticed/confirmed. Fatigue, drowsiness, breast tenderness, nausea, scent sensitivity. Almost no visible change.",
          "Show it as condition suddenly collapsing, not through appearance."),
    (84,  "late-early",
          "Nausea, mood swings, frequent urination may stand out. Belly still barely shows.",
          "\"More irritable than usual\", \"tears up out of nowhere\", \"goes to the bathroom often\"."),
    (112, "early-mid",
          "Nausea and fatigue ease. Belly starts to show just slightly.",
          "Ambiguous to others, but her own clothes start to feel tight."),
    (140, "mid",
          "Belly starts to look like a pregnant belly. Often feels the first fetal movement.",
          "First kicks: \"a light tap\", \"like a fish brushing past\", \"a bubble popping inside\"."),
    (168, "late-mid",
          "Belly noticeably out; lower-back/pelvis strain rises. Skin changes, stretch marks, breast changes possible.",
          "Hard to stand for long; conscious of the belly when sitting. Use kicks in emotional beats."),
    (196, "end-of-mid",
          "Sleep discomfort, back pain, belly tightening, indigestion may increase.",
          "\"Has to lie on her side\", \"rests a hand to cradle the belly\", \"walks slower\"."),
    (224, "early-late",
          "Shortness of breath, frequent urination, leg swelling, false contractions possible.",
          "Movement gets sluggish; stairs and long walks feel taxing."),
    (252, "late",
          "Pressure on stomach/lungs/bladder intensifies. Hard to sleep, tires easily.",
          "\"Bothersome to get up once seated\", \"pauses for breath mid-sentence\", \"belly tightens often\"."),
    (10_000, "near-term",
          "Belly feels dropped low. False labor, mucus plug, water breaking possible.",
          "Tension rising. Hospital bag, timing contractions, overprotective people around her."),
]


def render_reproductive_state(char_data: dict, npcs: list[dict]) -> str:
    """Render cycle phase, pregnancy stage, and contraception risk for present women.

    Returns an empty string when no present character tracks a cycle, so the caller
    can omit the block entirely. The caller is responsible for rendering this only
    while the adult engine is enabled.
    """
    entries: list[tuple[str, dict]] = []
    seen_names: set[str] = set()

    for source in [char_data, *npcs]:
        dyn = source.get("dynamic_state", {})
        name = source.get("name", "?")
        if dyn.get("has_menstrual_cycle") is True and name not in seen_names:
            entries.append((name, dyn))
            seen_names.add(name)

    if not entries:
        return ""

    lines: list[str] = []
    has_risk = False
    for name, dyn in entries:
        status = _cycle_status(dyn)
        lines.append(f"{name}: {status}")
        if "pregnancy_risk=있음" in status:
            has_risk = True
    if has_risk:
        lines.append(
            "pregnancy_risk=있음 캐릭터가 있다. 피임이 생략된 접촉이면 본문 안에서 그 사실이 드러나게 한다."
        )
    return _render_prompt_block("reproductive_state", "\n".join(lines))


def _cycle_status(dyn_state: dict) -> str:
    """단일 캐릭터의 생리 주기 상태를 한 줄로 반환합니다."""
    cycle_day = int(dyn_state.get("cycle_day") or 1)
    pregnant  = bool(dyn_state.get("pregnant") or False)
    preg_day  = int(dyn_state.get("pregnancy_day") or 0)
    contraception = normalize_contraception_value(dyn_state.get("contraception"))

    if pregnant:
        stage, body, hint = next(
            ((s, body, hint) for max_day, s, body, hint in _PREGNANCY_STAGES if preg_day <= max_day),
            _PREGNANCY_STAGES[-1][1:],
        )
        return (
            f"pregnant [{stage}] {body} CUES: {hint} "
            "NOTE: never count or state exact days/weeks - people do not recall dates precisely."
        )

    phase_ranges = {
        range(1, 6):  ("생리 중", False),
        range(6, 10): ("난포기",  False),
        range(10, 18):("가임기",  True),
        range(18, 29):("황체기",  False),
    }
    phase, fertile = next(
        (v for r, v in phase_ranges.items() if cycle_day in r),
        ("황체기", False),
    )
    if not fertile:
        risk = "없음"
    elif contraception == "oral":
        risk = "낮음" + (" (배란 피크)" if cycle_day == 14 else "")
    else:
        risk = "있음" + (" (배란 피크)" if cycle_day == 14 else "")
    # cycle_day 정수는 노출하지 않는다(국면·가임 위험만으로 충분; "N일째" 받아쓰기 방지).
    return f"{phase} / pregnancy_risk={risk}"


# ----------------
# Dynamic state / context renderers
# ----------------

def render_state_line(dyn_state: dict, world_config: dict | None = None) -> str:
    """Render selected DynamicState fields as one compact checklist line."""
    fields = list(_DEFAULT_STATE_FIELDS)
    fields.extend((world_config or {}).get("extra_state_fields", []))

    parts = []
    for key, label, skip_if in fields:
        val = dyn_state.get(key)
        if val is None or val in skip_if:
            continue
        parts.append(f"{label}={val}")

    return " | ".join(parts) if parts else "없음"


def clean_prompt_dict(data: dict) -> dict:
    """Remove internal keys and null values before injecting graph records into prompt."""
    cleaned: dict = {}
    for key, value in data.items():
        if key.startswith("_") or value is None:
            continue
        if isinstance(value, dict):
            nested = clean_prompt_dict(value)
            if nested:
                cleaned[key] = nested
            continue
        if isinstance(value, list):
            nested_list = [
                clean_prompt_dict(item) if isinstance(item, dict) else item
                for item in value
                if item is not None
            ]
            if nested_list:
                cleaned[key] = nested_list
            continue
        cleaned[key] = value
    return cleaned


def join_rendered_context(rendered_context: dict[str, str]) -> str:
    """Join pre-rendered dynamic context blocks in stable prompt order."""
    order = (
        "scene",
        "state",
        "relationship",
        "personal_facts",
        "npcs",
        "events",
        "memories",
        "narrative_log",
        "world",
    )
    blocks = [rendered_context.get(key, "") for key in order]
    return "<world_context>\n" + "\n\n".join(block for block in blocks if block) + "\n</world_context>"


def render_active_characters_section(
    char_data: dict,
    user_data: dict,
    npcs: list[dict],
    scene_types: list[str],
) -> str:
    """Render all currently active character prompt data as Markdown."""
    character_blocks: list[str] = []
    seen_ids: set[str] = set()

    for role, data in (
        ("PC / POV candidate", user_data),
        ("Primary NPC", char_data),
    ):
        block = _render_character_markdown(role, data, scene_types)
        char_id = str(data.get("id") or data.get("name") or "").strip()
        if block and char_id not in seen_ids:
            character_blocks.append(block)
            seen_ids.add(char_id)

    for npc in npcs:
        char_id = str(npc.get("id") or npc.get("name") or "").strip()
        if char_id in seen_ids:
            continue
        block = _render_character_markdown("Present NPC", _normalize_npc_prompt_data(npc), scene_types)
        if block:
            character_blocks.append(block)
            seen_ids.add(char_id)

    if not character_blocks:
        return ""
    return "<active_characters>\n" + "\n\n---\n\n".join(character_blocks) + "\n</active_characters>"


def render_header(location: str, dt: datetime) -> str:
    """Render the date, time, and current location header."""
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return (
        f"**{dt.year}년 {dt.month}월 {dt.day}일 {weekdays[dt.weekday()]}요일 "
        f"{dt.hour:02d}시 {dt.minute:02d}분, {location}**"
    )


def inject_node(node: dict) -> str:
    """Convert any graph node with name/description/prompt_hint to prompt-injectable text."""
    parts = []
    if name := node.get("name"):
        parts.append(f"[{name}]")
    if hint := node.get("prompt_hint"):
        parts.append(hint)
    else:
        if desc := node.get("description"):
            parts.append(desc)
        if atm := node.get("atmosphere"):
            parts.append(f"Atmosphere: {atm}")
    return "\n".join(parts)


def render_location_context(location_nodes: list[dict]) -> str:
    """Render location hierarchy (most specific → most general) for dynamic prompt injection."""
    if not location_nodes:
        return ""
    injected = [
        inject_node(n)
        for n in location_nodes
        if n.get("prompt_hint") or n.get("description")
    ]
    if not injected:
        return ""
    return "<location_context>\n" + "\n\n".join(injected) + "\n</location_context>"


# ----------------
# Internal render helpers
# ----------------

def _normalize_npc_prompt_data(npc: dict) -> dict:
    """Map secondary NPC records to the same prompt keys as primary characters."""
    normalized = dict(npc)
    if "profile" in npc and "static_profile" not in normalized:
        normalized["static_profile"] = npc["profile"]
    return normalized


def _filter_dynamic_state(state: dict | None) -> dict | None:
    """Remove internal or prompt-suppressed dynamic-state fields."""
    if not state:
        return state
    s = dict(state)
    for key in _PROMPT_HIDDEN_STATE_KEYS:
        s.pop(key, None)
    s.pop("has_menstrual_cycle", None)
    s.pop("location_id", None)
    if s.get("pregnant"):
        s.pop("cycle_day", None)
    else:
        s.pop("pregnancy_day", None)
        s.pop("pregnant", None)
    return s


def _render_character_markdown(role: str, data: dict, scene_types: list[str]) -> str:
    """Render one character's prompt sections as Markdown."""
    if not data:
        return ""
    name = str(data.get("name") or data.get("id") or "?")
    char_id = str(data.get("id") or "")
    title = f"## {role}: {name}" + (f" ({char_id})" if char_id and char_id != name else "")
    sections: list[str] = [title]

    _append_markdown_section(sections, "Static Profile", data.get("static_profile"))
    _append_markdown_section(sections, "Dynamic Information", data.get("dynamic_information"))
    _append_markdown_section(sections, "Personality", data.get("personality"))
    _append_markdown_section(sections, "Dynamic State", _filter_dynamic_state(data.get("dynamic_state")))
    if "intimate" in scene_types:
        _append_markdown_section(sections, "Intimate Profile", data.get("intimate_profile"))
    if "workplace" in scene_types:
        _append_markdown_section(sections, "Workplace Profile", data.get("workplace_profile"))
    _append_markdown_section(sections, "Speech Profiles", data.get("speech_profiles"))
    _append_markdown_section(sections, "Relationship Profiles", data.get("relationship_profiles"))

    return "\n\n".join(sections)


def _append_markdown_section(sections: list[str], title: str, value: object) -> None:
    """Append a non-empty Markdown subsection."""
    if value in (None, "", [], {}):
        return
    cleaned = clean_prompt_dict(value) if isinstance(value, dict) else value
    if cleaned in (None, "", [], {}):
        return
    sections.append(f"### {title}\n{_markdown_value(cleaned)}")


def _markdown_value(value: object, indent: int = 0) -> str:
    """Render nested prompt data as readable Markdown bullets."""
    prefix = "  " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}- {key}:")
                lines.append(_markdown_value(item, indent + 1))
            else:
                lines.append(f"{prefix}- {key}: {item}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_markdown_value(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
        return "\n".join(lines)
    return f"{prefix}{value}"


