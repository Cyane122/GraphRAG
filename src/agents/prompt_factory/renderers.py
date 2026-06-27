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
#   - build_genre_section(genres: list[str], world_config: dict | None) -> str : 장르별 프롬프트 렌더링
#   - render_state_line(dyn_state: dict, world_config: dict | None) -> str : 상태 한 줄 렌더링
#   - clean_prompt_dict(data: dict) -> dict : 내부 키·null 값 제거
#   - join_rendered_context(rendered_context: dict[str, str]) -> str : 동적 컨텍스트 블록 결합
#   - render_active_characters_section(char_data: dict, user_data: dict, npcs: list[dict], scene_types: list[str]) -> str : 현재 등장 캐릭터 프로필 렌더링 (전체 필드)
#   - render_header(location: str, dt: datetime) -> str : 날짜·시간·장소 헤더 렌더링
# ================================

from datetime import datetime
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_PROMPT_HIDDEN_STATE_KEYS: frozenset[str] = frozenset({
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

    The tag parameter may include attributes, e.g. 'genre name="intimate"'.
    Closing tag uses only the tag name (before the first space).
    """
    if not body:
        return ""
    return f"<{tag}>\n{body.strip()}\n</{tag.split()[0]}>"


# ----------------
# Genre prompt
# ----------------

def build_genre_section(genres: list[str], world_config: dict | None = None) -> str:
    """Render common genre-specific protocol blocks.

    Genre md files are tagless. Tags are added here.

    Rules:
    - This renderer only reads common prompt_factory genre prompt.
    - World-specific scene prompt are handled by PromptBuilder.build_scene_specific_prompt().
    - World prompt/scenes/{scene_type}.md overrides prompt_factory default scene prompt.
    - No world-specific genre key such as intimate_sses is handled here.
    - Missing genre body is omitted.
    """
    parts = []

    for genre in genres:
        body = _read_optional_prompt(f"genre_specific/{genre}.md")
        if body:
            parts.append(_render_prompt_block(f'genre name="{genre}"', body))

    return "\n\n".join(parts)


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


