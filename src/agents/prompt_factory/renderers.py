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
#   - _read_optional_prompt(relative_path: str) -> str : prompts/ 하위 Markdown 파일 읽기
#   - _render_prompt_block(tag: str, body: str) -> str : 본문을 XML 블록으로 감싸기
#   - build_genre_section(genres: list[str], world_config: dict | None) -> str : 장르별 프롬프트 렌더링
#   - render_state_line(dyn_state: dict, world_config: dict | None) -> str : 상태 한 줄 렌더링
#   - clean_prompt_dict(data: dict) -> dict : 내부 키·null 값 제거
#   - join_rendered_context(rendered_context: dict[str, str]) -> str : 동적 컨텍스트 블록 결합
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
    - This renderer only reads common prompt_factory genre prompts.
    - World-specific scene prompts are handled by PromptBuilder.build_scene_specific_prompt().
    - World prompt/scenes/{scene_type}.md overrides prompt_factory default scene prompts.
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
    """Remove internal keys and null values before injecting graph records into prompts."""
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
