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
#   - render_character_section(char_data: dict, scene_types: list[str]) -> str : 캐릭터 프로필 렌더링
#   - render_npc_section(npcs: list[dict], char_name: str) -> str : 보조 NPC 렌더링
#   - render_relationship_section(relationship: dict) -> str : 관계 레코드 렌더링
#   - render_events_section(events: list[dict], char_name: str, user_name: str) -> str : 최근 이벤트 렌더링
#   - render_recall_events_section(recall_events: list[dict], memory_conflicts: list[str] | None) -> str : 회상 이벤트 렌더링
#   - render_world_section(world_context: dict) -> str : 월드 컨텍스트 렌더링
#   - render_header(location: str, dt: datetime) -> str : 날짜·시간·장소 헤더 렌더링
# ================================

from datetime import datetime
from pathlib import Path
import json

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


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


def render_character_section(char_data: dict, scene_types: list[str]) -> str:
    """Render the main NPC profile and scene-specific profile fragments."""
    sections = []
    if "static_profile" in char_data:
        sections.append(_json_block("static", clean_prompt_dict(char_data["static_profile"])))
    if "dynamic_information" in char_data:
        sections.append(_json_block("info", clean_prompt_dict(char_data["dynamic_information"])))
    if "personality" in char_data:
        sections.append(_json_block("personality", clean_prompt_dict(char_data["personality"])))
    if "dynamic_state" in char_data:
        sections.append(_json_block("state", clean_prompt_dict(char_data["dynamic_state"])))
    if "intimate" in scene_types and "intimate_profile" in char_data:
        sections.append(_json_block("intimate", clean_prompt_dict(char_data["intimate_profile"])))
    if "workplace" in scene_types and "workplace_profile" in char_data:
        sections.append(_json_block("workplace", clean_prompt_dict(char_data["workplace_profile"])))
    return "<character>\n" + "\n".join(sections) + "\n</character>"


def render_npc_section(npcs: list[dict], char_name: str) -> str:
    """Render secondary NPC profiles and their relationship with the main NPC."""
    if not npcs:
        return ""
    blocks = []
    for npc in npcs:
        name = npc.get("name", "?")
        sections = []
        if npc.get("profile"):
            sections.append(_json_block("static", clean_prompt_dict(npc["profile"])))
        if npc.get("dynamic_information"):
            sections.append(_json_block("info", clean_prompt_dict(npc["dynamic_information"])))
        if npc.get("personality"):
            sections.append(_json_block("personality", clean_prompt_dict(npc["personality"])))
        if npc.get("dynamic_state"):
            sections.append(_json_block("state", clean_prompt_dict(npc["dynamic_state"])))
        if npc.get("speech_profiles"):
            sections.append(_json_block("speech_profiles", clean_prompt_dict({"items": npc["speech_profiles"]})))
        if npc.get("relationship_profiles"):
            sections.append(_json_block("relationship_profiles", clean_prompt_dict({"items": npc["relationship_profiles"]})))
        rel = npc.get("rel_to_npc", {})
        if rel:
            rel_str = json.dumps(rel, ensure_ascii=False, indent=2)
            sections.append(
                f"<relationship_with_{char_name}>\n{rel_str}\n</relationship_with_{char_name}>"
            )
        blocks.append(f"<npc name=\"{name}\">\n" + "\n".join(sections) + "\n</npc>")
    return "<npcs>\n" + "\n\n".join(blocks) + "\n</npcs>"


def render_relationship_section(relationship: dict) -> str:
    """Render the relationship record as JSON."""
    if not relationship:
        return ""
    return _json_block("relationship", relationship)


def render_events_section(events: list[dict], char_name: str, user_name: str) -> str:
    """Render recent events and character-specific memory summaries."""
    if not events:
        return "<recent_events>없음</recent_events>"
    lines = []
    for event in events:
        status = event.get("status") or "closed"
        if status == "active":
            turns = event.get("turn_count") or 1
            label = f"[진행중 {turns}턴]"
            line = f"- [{event.get('timestamp', '?')}] {label} {event.get('summary', '')}"
            # active 이벤트는 최근 내용 미리보기 포함
            content = (event.get("content") or "").strip()
            if content:
                preview = content[-1200:]  # 마지막 1200자 (최근 턴 중심)
                line += f"\n  [최근 내용]\n  {preview}"
        else:
            line = f"- [{event.get('timestamp', '?')}] {event.get('summary', '')}"
        npc_mem = event.get("npc_memory")
        pc_mem = event.get("pc_memory")
        if npc_mem:
            line += f"\n  {char_name} 기억: {npc_mem}"
        if pc_mem:
            line += f"\n  {user_name} 기억: {pc_mem}"
        lines.append(line)
    return "<recent_events>\n" + "\n".join(lines) + "\n</recent_events>"


def render_recall_events_section(
    recall_events: list[dict],
    memory_conflicts: list[str] | None = None,
) -> str:
    """Render vector-recalled past events for the current turn."""
    if not recall_events:
        return ""
    lines = []
    for event in recall_events:
        marker = " [MEMORY_CONFLICT]" if event.get("conflict") else ""
        lines.append(f"- {event.get('summary', '')}{marker}")
    block = "<recall_events>\n" + "\n".join(lines) + "\n</recall_events>"

    if memory_conflicts:
        conflict_hint = (
            "<!-- MEMORY_CONFLICT detected: NPC's memory of this event differs "
            "from what the user may believe. React with mild natural confusion if "
            "the user's version contradicts the NPC's memory. "
            "One soft correction max, then move on. -->"
        )
        block = conflict_hint + "\n" + block
    return block


def render_world_section(world_context: dict) -> str:
    """Render legacy world context when no pre-rendered context is provided."""
    parts: list[str] = []
    _append_scene_state(parts, world_context.get("scene_state", {}))
    _append_context_plan(parts, world_context.get("context_plan", {}))
    _append_rules(parts, world_context.get("rules", []))
    _append_static_events(parts, world_context.get("static_events", []))
    _append_routine_schedules(parts, world_context.get("routine_schedules", []))
    _append_schedules(parts, world_context.get("schedules", []))
    _append_nearby_activity(parts, world_context.get("nearby_activity", []))
    _append_sns(parts, world_context.get("sns_posts", []))
    kakao_turn_context = world_context.get("kakao_turn_context", {})
    _append_kakao_turn_context(parts, kakao_turn_context)
    if not kakao_turn_context:
        _append_kakao(parts, world_context.get("kakao_rooms", []))
    _append_goals(parts, world_context.get("life_goals", []))
    _append_item_memories(parts, world_context.get("object_memories", []))
    _append_secrets(parts, world_context.get("secret_hints", []))
    return "<world_context>\n" + "\n\n".join(parts) + "\n</world_context>" if parts else ""


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

def _json_block(tag: str, data: dict) -> str:
    """Render a JSON-backed XML-ish prompt block."""
    return f"<{tag}>\n{json.dumps(data, ensure_ascii=False, indent=2)}\n</{tag}>"


def _normalize_npc_prompt_data(npc: dict) -> dict:
    """Map secondary NPC records to the same prompt keys as primary characters."""
    normalized = dict(npc)
    if "profile" in npc and "static_profile" not in normalized:
        normalized["static_profile"] = npc["profile"]
    return normalized


def _filter_dynamic_state(state: dict | None) -> dict | None:
    if not state:
        return state
    s = dict(state)
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


def _append_scene_state(parts: list[str], scene_state: dict) -> None:
    """Append scene continuity hints to the fallback world context."""
    if not scene_state:
        return
    participants = ", ".join(scene_state.get("participants", [])) or "unknown"
    parts.append(
        "[Current Scene]\n"
        f"- Location: {scene_state.get('location', 'unknown')}\n"
        f"- Participants: {participants}\n"
        f"- Mood: {scene_state.get('mood', 'neutral')}\n"
        f"- Tension: {scene_state.get('tension', 0.0)}"
    )


def _append_context_plan(parts: list[str], context_plan: dict) -> None:
    """Append context planner scope hints to the fallback world context."""
    if not context_plan:
        return
    focus = ", ".join(context_plan.get("query_focus", [])) or "current_scene"
    skipped = ", ".join(context_plan.get("skip_systems", [])) or "none"
    parts.append(
        "[Context Scope]\n"
        f"- Importance: {context_plan.get('importance', 0)}\n"
        f"- Focus: {focus}\n"
        f"- Skipped systems: {skipped}"
    )


def _append_static_events(parts: list[str], static_events: list[dict]) -> None:
    """Append scheduled or active static events."""
    if not static_events:
        return
    lines = []
    for event in static_events:
        label = "오늘" if event.get("status") == "active" else "예정"
        lines.append(f"- [{label}] {event.get('hint', '')}")
    parts.append("[Upcoming Events]\n" + "\n".join(lines))


def _append_rules(parts: list[str], rules: list[dict]) -> None:
    """Append generic active prompt rules."""
    if not rules:
        return
    lines = []
    for rule in rules[:4]:
        name = rule.get("name") or rule.get("id") or "rule"
        hint = rule.get("prompt_hint") or rule.get("summary") or ""
        if hint:
            lines.append(f"- {name}: {hint}")
    if lines:
        parts.append("[Active Rules]\n" + "\n".join(lines))


def _append_schedules(parts: list[str], schedules: list[dict]) -> None:
    """Append active or soon-upcoming character schedules."""
    if not schedules:
        return
    lines = []
    for schedule in schedules[:4]:
        owner = schedule.get("owner_name") or schedule.get("owner_id") or "character"
        timing = schedule.get("timing") or "today"
        start = schedule.get("start_time") or "today"
        end = schedule.get("end_time") or ""
        time_range = f"{start}-{end}" if end else start
        name = schedule.get("name") or schedule.get("activity") or "schedule"
        hint = schedule.get("prompt_hint") or schedule.get("summary") or schedule.get("activity") or ""
        location = schedule.get("location_name") or schedule.get("location_id") or ""
        location_part = f" @ {location}" if location else ""
        material = _render_schedule_material(schedule.get("material"))
        line = f"- [{timing}] {owner}: {time_range}{location_part} {name}. {hint}".strip()
        pressure = _render_schedule_pressure(schedule)
        if pressure:
            line += f" {pressure}"
        if material:
            line += f" Material: {material}"
        lines.append(line)
    lines.append("- If a material includes lyric text, use only that lyric and do not invent or continue real lyrics.")
    parts.append("[Schedules]\n" + "\n".join(lines))


def _append_routine_schedules(parts: list[str], schedules: list[dict]) -> None:
    """Append minimal always-on routine schedule knowledge."""
    if not schedules:
        return
    lines = []
    for schedule in schedules[:6]:
        lines.append(_render_routine_schedule(schedule))
    lines.append("- This block is stable routine knowledge: use it to answer schedule questions, but do not assume the character is there unless it is today/current.")
    parts.append("[Routine Schedules]\n" + "\n".join(lines))


def _render_routine_schedule(schedule: dict) -> str:
    """Render minimal recurring schedule info without detailed material."""
    owner = schedule.get("owner_name") or schedule.get("owner_id") or "character"
    days = _weekday_label(schedule)
    start = schedule.get("start_time") or "today"
    end = schedule.get("end_time") or ""
    time_range = f"{start}-{end}" if end else start
    name = schedule.get("name") or schedule.get("activity") or "schedule"
    location = schedule.get("location_name") or schedule.get("location_id") or ""
    location_part = f" @ {location}" if location else ""
    today = " today" if schedule.get("is_today") else ""
    return f"- [{days}{today}] {owner}: {time_range}{location_part} {name}".strip()


def _render_schedule_pressure(schedule: dict) -> str:
    """Render urgent schedule pressure for the actor prompt."""
    timing = schedule.get("timing")
    if timing == "active":
        return "이미 시작 시간이다. 지체하면 어색하므로 빨리 가야 한다."
    if timing != "upcoming":
        return ""
    try:
        minutes_until = float(schedule.get("minutes_until"))
    except (TypeError, ValueError):
        return ""
    if minutes_until <= 30:
        return f"시간이 임박했다({int(minutes_until)}분 남음). 빨리 가야 한다."
    return ""


def _weekday_label(schedule: dict) -> str:
    """Render weekday metadata into a compact label."""
    if schedule.get("recurrence") == "daily":
        return "daily"
    if schedule.get("date"):
        return str(schedule["date"])
    days = schedule.get("day_of_weeks") or []
    if not days and schedule.get("day_of_week") not in (None, "", -1):
        days = [schedule.get("day_of_week")]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    labels = []
    for day in days:
        try:
            labels.append(names[int(day)])
        except (TypeError, ValueError, IndexError):
            continue
    return "/".join(labels) if labels else "routine"


def _render_schedule_material(raw_material: object) -> str:
    """Render optional Schedule.material JSON/string into a compact prompt hint."""
    if not raw_material:
        return ""
    if isinstance(raw_material, dict):
        material = raw_material
    elif isinstance(raw_material, str):
        try:
            parsed = json.loads(raw_material)
        except json.JSONDecodeError:
            return raw_material
        material = parsed if isinstance(parsed, dict) else {}
    else:
        return ""

    parts = []
    title = material.get("song_title") or material.get("title")
    artist = material.get("artist")
    if title:
        parts.append(f"{title}" + (f" by {artist}" if artist else ""))
    focus = material.get("practice_focus") or material.get("focus")
    if focus:
        if isinstance(focus, list):
            focus_text = ", ".join(str(item) for item in focus[:4])
        else:
            focus_text = str(focus)
        parts.append(f"focus={focus_text}")
    lyric = material.get("lyric")
    if lyric:
        parts.append(f"lyric={lyric}")
    korean_accent = material.get("korean_accent")
    if korean_accent:
        parts.append(f"korean_accent={korean_accent}")
    policy = material.get("policy")
    if policy:
        parts.append(f"policy={policy}")
    return "; ".join(parts)


def _append_nearby_activity(parts: list[str], nearby: list[dict]) -> None:
    """Append nearby character or location activity."""
    if nearby:
        parts.append("[Nearby Activity]\n" + "\n".join(f"- {a['name']}: {a['summary']}" for a in nearby))


def _append_sns(parts: list[str], sns_posts: list[str]) -> None:
    """Append SNS feed snippets."""
    if sns_posts:
        parts.append("[SNS Feed]\n" + "\n".join(f"- {post}" for post in sns_posts))


def _append_kakao(parts: list[str], kakao_rooms: list[dict]) -> None:
    """Append KakaoTalk room snippets."""
    if not kakao_rooms:
        return
    room_blocks: list[str] = []
    for room in kakao_rooms:
        name = room.get("room") or "톡방"
        members = ", ".join(room.get("members") or [])
        topic = room.get("topic") or ""
        header = f"- {name}"
        if members:
            header += f" ({members})"
        if topic:
            header += f": {topic}"
        messages = [
            f"  - {message}"
            for message in room.get("recent_messages") or []
            if message
        ]
        room_blocks.append("\n".join([header, *messages]))
    if room_blocks:
        parts.append("[KakaoTalk Rooms]\n" + "\n".join(room_blocks))


def _append_kakao_turn_context(parts: list[str], kakao_turn_context: dict) -> None:
    """Append this-turn KakaoTalk messages as context, not output format."""
    messages = kakao_turn_context.get("messages") if isinstance(kakao_turn_context, dict) else []
    if not messages:
        return
    lines = [
        "[KakaoTalk Before Current Input]",
        "- These KakaoTalk messages are situation context only.",
        "- Actor must not output KakaoTalk chat UI, chat logs, timestamps, or message bubbles.",
    ]
    for message in messages:
        room = message.get("room") or "톡방"
        sender = message.get("sender_name") or message.get("sender_id") or "unknown"
        content = message.get("content") or ""
        timestamp = message.get("timestamp") or ""
        time_label = timestamp[11:16] if len(timestamp) >= 16 else timestamp
        prefix = f"- {time_label} " if time_label else "- "
        lines.append(f"{prefix}{room} / {sender}: {content}")
    parts.append("\n".join(lines))


def _append_goals(parts: list[str], goals: list[dict]) -> None:
    """Append long-running goal hints."""
    if not goals:
        return
    lines = []
    for goal in goals:
        title = goal.get("title", "?")
        hint = goal.get("hint") or goal.get("next_hint") or ""
        subtlety = goal.get("subtlety", "?")
        lines.append(f"- {title} (subtlety={subtlety}): {hint}")
    parts.append("[Life Goals]\n" + "\n".join(lines))


def _append_item_memories(parts: list[str], item_memories: list[dict]) -> None:
    """Append object-linked memory hints."""
    if not item_memories:
        return
    lines = []
    for item in item_memories:
        name = item.get("name") or item.get("item_name") or item.get("item_id") or "?"
        memory = item.get("memory") or item.get("memory_summary") or item.get("summary") or item.get("hint") or ""
        lines.append(f"- {name}: {memory}")
    parts.append("[Object Memories]\n" + "\n".join(lines))


def _append_secrets(parts: list[str], secrets: list[dict]) -> None:
    """Append subtext hints for partially revealed secrets."""
    if not secrets:
        return
    lines = []
    for secret in secrets:
        title = secret.get("title", "?")
        hint = secret.get("hint") or secret.get("public_hint") or ""
        level = secret.get("reveal_level", secret.get("current_reveal_level", 0))
        lines.append(f"- {title} (reveal_level={level}): {hint}")
    parts.append("[Subtext]\n" + "\n".join(lines))
