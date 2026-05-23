# ================================
# src/agents/context/renderer.py
#
# Dynamic context budget and renderer utilities for Actor prompts.
#
# Functions
#   - build_rendered_dynamic_context(scene_state: dict, context_plan: dict, relationship: dict, events: list[dict], recall_events: list[dict], personal_facts: list[dict], npcs: list[dict], world_context: dict, dynamic_state: dict | None = None) -> dict[str, str] : render dynamic context blocks
# ================================

import json

from src.simulation.systems.personal_facts import render_personal_facts


DEFAULT_CONTEXT_BUDGET: dict[str, int] = {
    "scene": 300,
    "state": 350,
    "characters": 500,
    "location": 250,
    "rules": 250,
    "memories": 700,
    "personal_facts": 600,
    "relationships": 400,
    "schedules": 250,
    "goals": 300,
    "items": 250,
    "subtext": 300,
    "recent_summary": 600,
    "narrative_log": 800,
}


def build_rendered_dynamic_context(
    scene_state: dict,
    context_plan: dict,
    relationship: dict,
    events: list[dict],
    recall_events: list[dict],
    personal_facts: list[dict],
    npcs: list[dict],
    world_context: dict,
    dynamic_state: dict | None = None,
) -> dict[str, str]:
    """Render DB records into budgeted Actor-facing dynamic context blocks."""
    budget = dict(DEFAULT_CONTEXT_BUDGET)
    budget.update(context_plan.get("budget") or {})
    blocks = {
        "scene": _render_scene_block(scene_state, context_plan, budget["scene"]),
        "state": _render_numeric_state_block(dynamic_state or {}, budget["state"]),
        "relationship": _render_relationship_block(relationship, budget["relationships"]),
        "personal_facts": _clamp_block(render_personal_facts(personal_facts), budget["personal_facts"]),
        "npcs": _render_npc_block(npcs, budget["characters"]),
        "events": _render_events_block(events, budget["recent_summary"]),
        "memories": _render_memory_block(recall_events, budget["memories"]),
        "narrative_log": _render_narrative_log_block(world_context.get("narrative_log", ""), budget["narrative_log"]),
        "world": _render_world_block(world_context, budget, relationship),
    }
    return {key: value for key, value in blocks.items() if value}


def _render_scene_block(scene_state: dict, context_plan: dict, budget: int) -> str:
    """Render current scene continuity and planner scope."""
    participants = ", ".join(scene_state.get("participants", [])) or "unknown"
    lines = [
        "[Current Scene]",
        f"- Location: {scene_state.get('location', 'unknown')}",
        f"- Participants: {participants}",
        f"- Scene type: {scene_state.get('scene_type', context_plan.get('scene_type', 'daily'))}",
        f"- Mood / tension / distance: {scene_state.get('mood', 'neutral')} / {scene_state.get('tension', 0.0)} / {scene_state.get('physical_distance', 'normal')}",
    ]
    if scene_state.get("last_action"):
        lines.append(f"- Last action: {scene_state['last_action']}")
    beats = scene_state.get("unresolved_beats") or []
    if beats:
        lines.append("- Unresolved beats:")
        lines.extend(f"  - {beat}" for beat in beats[:5])

    focus = ", ".join(context_plan.get("query_focus", [])) or "current_scene"
    skipped = ", ".join(context_plan.get("skip_systems", [])) or "none"
    lines.extend([
        "",
        "[Context Scope]",
        f"- Importance: {context_plan.get('importance', 0)}",
        f"- Focus: {focus}",
        f"- Skipped systems: {skipped}",
        "- Use active hints only; do not mention skipped systems unless the user brings them into the scene.",
    ])
    return _clamp_block("\n".join(lines), budget)


def _render_relationship_block(relationship: dict, budget: int) -> str:
    """Render relationship record into concise hints."""
    if not relationship:
        return ""
    hints: list[str] = []
    band_line = _render_relationship_bands(relationship)
    if band_line:
        hints.append(f"- bands: {band_line}")
    for key in ("current_status", "status", "summary", "tone", "boundary", "conflict", "type", "affinity", "trust", "last_interaction"):
        if key in relationship and relationship[key] not in (None, ""):
            hints.append(f"- {key}: {relationship[key]}")
    if not hints:
        hints = [f"- {_compact_json(relationship, 320)}"]
    return _clamp_block("[Relationship]\n" + "\n".join(hints), budget)


def _render_npc_block(npcs: list[dict], budget: int) -> str:
    """Render secondary NPC context without dumping full records."""
    if not npcs:
        return ""
    lines = ["[Active Secondary Characters]"]
    for npc in npcs[:4]:
        name = npc.get("name") or npc.get("char_id") or "unknown"
        profile = npc.get("profile") or {}
        info = npc.get("dynamic_information") or {}
        personality = npc.get("personality") or {}
        rel = npc.get("rel_to_npc") or {}
        speech_hint = _first_profile_hint(npc.get("speech_profiles") or [])
        relationship_hint = _first_profile_hint(npc.get("relationship_profiles") or [])
        profile_hint = _first_present(profile, ("prompt_hint", "summary", "role", "personality"))
        info_hint = _first_present(info, ("prompt_hint", "summary", "personality", "current_reputation"))
        personality_hint = _first_present(personality, ("prompt_hint", "summary", "speech_style", "core_traits"))
        rel_hint = _first_present(rel, ("prompt_hint", "summary", "current_status", "status", "affinity"))
        line = f"- {name}"
        hints = [hint for hint in (profile_hint, info_hint, personality_hint, speech_hint) if hint]
        if hints:
            line += f": {' '.join(str(hint) for hint in hints[:3])}"
        if relationship_hint:
            line += f" Relationship profile: {relationship_hint}"
        elif rel_hint:
            line += f" Relationship to main NPC: {rel_hint}"
        lines.append(line)
    return _clamp_block("\n".join(lines), budget)


def _render_events_block(events: list[dict], budget: int) -> str:
    """Render recent events as story continuity hints."""
    if not events:
        return ""
    lines = ["[Recent Story]"]
    for event in events[:4]:
        timestamp = event.get("timestamp") or "recent"
        summary = event.get("summary") or ""
        if not summary:
            continue
        memory_type = event.get("memory_type")
        type_label = f" {memory_type}" if memory_type else ""
        line = f"- [{timestamp}{type_label}] {summary}"
        if event.get("npc_memory"):
            line += f" NPC remembers: {event['npc_memory']}"
        lines.append(line)
    return _clamp_block("\n".join(lines), budget)


def _render_memory_block(recall_events: list[dict], budget: int) -> str:
    """Render recalled memories ordered by relevance."""
    if not recall_events:
        return ""
    ranked = sorted(recall_events, key=lambda item: float(item.get("score") or 0), reverse=True)
    lines = ["[Memories]"]
    for memory in ranked[:5]:
        summary = memory.get("summary") or ""
        if not summary:
            continue
        conflict = " Possible memory conflict." if memory.get("conflict") else ""
        memory_type = memory.get("memory_type")
        prefix = f"[{memory_type}] " if memory_type else ""
        lines.append(f"- {prefix}{summary}{conflict}")
    return _clamp_block("\n".join(lines), budget)


def _render_narrative_log_block(narrative_log: str, budget: int) -> str:
    """Render compressed narrative timeline log."""
    if not narrative_log:
        return ""
    return _clamp_block(f"[Timeline Log]\n{narrative_log}", budget)


def _render_numeric_state_block(dynamic_state: dict, budget: int) -> str:
    """Render DynamicState numbers as stable prompt bands."""
    if not dynamic_state:
        return ""

    lines = ["[Dynamic State Bands]"]
    mood = dynamic_state.get("mood") or dynamic_state.get("emotional_state")
    if mood:
        lines.append(f"- mood: {mood} ({_mood_band(str(mood))})")

    stress = _first_numeric(dynamic_state, ("stress_level", "stress", "workplace_stress_level"))
    if stress is not None:
        lines.append(f"- stress: {_format_number(stress)} ({_stress_band(stress)})")

    ts_acceptance = _first_numeric(dynamic_state, ("ts_acceptance",))
    if ts_acceptance is not None:
        lines.append(
            f"- ts_acceptance: {_format_number(ts_acceptance)} "
            f"({_percent_band(ts_acceptance, 'resistant', 'uneasy', 'tentative', 'receptive', 'internalized')})"
        )

    need_parts = []
    for key in ("hunger", "rest", "social", "fun", "safety", "libido"):
        value = _as_float(dynamic_state.get(key))
        if value is not None:
            need_parts.append(f"{key}={_need_band(value)}")
    if need_parts:
        lines.append(f"- needs: {', '.join(need_parts)}")

    if len(lines) == 1:
        return ""
    lines.append("- Use bands as expression intensity; do not rewrite persistent prompt_hint from small numeric changes.")
    return _clamp_block("\n".join(lines), budget)


def _render_world_block(world_context: dict, budget: dict[str, int], relationship: dict | None = None) -> str:
    """Render optional world context blocks according to per-block budgets."""
    parts: list[str] = []
    if world_context.get("location_profile"):
        location = world_context["location_profile"]
        hint = _first_present(location, ("prompt_hint", "summary", "description", "atmosphere"))
        if hint:
            name = location.get("name") or location.get("id") or "current location"
            parts.append(_clamp_block(f"[Location]\n- {name}: {hint}", budget["location"]))

    if world_context.get("rules"):
        lines = ["[Active Rules]"]
        for rule in world_context["rules"][:4]:
            name = rule.get("name") or rule.get("id") or "rule"
            hint = _first_present(rule, ("prompt_hint", "summary"))
            if hint:
                lines.append(f"- {name}: {hint}")
        if len(lines) > 1:
            parts.append(_clamp_block("\n".join(lines), budget["rules"]))

    if world_context.get("speech_profiles"):
        lines = ["[Speech Profile]"]
        for profile in world_context["speech_profiles"][:3]:
            name = profile.get("name") or profile.get("id") or "speech"
            hint = _first_present(profile, ("prompt_hint", "summary"))
            if hint:
                lines.append(f"- {name}: {hint}")
        if len(lines) > 1:
            parts.append(_clamp_block("\n".join(lines), budget["characters"]))

    if world_context.get("relationship_profiles"):
        lines = ["[Relationship Profile]"]
        band_line = _render_relationship_bands(relationship or {})
        for profile in world_context["relationship_profiles"][:3]:
            name = profile.get("name") or profile.get("id") or "relationship"
            hint = _first_present(profile, ("prompt_hint", "summary"))
            if hint:
                suffix = f" Current bands: {band_line}." if band_line else ""
                lines.append(f"- {name}: {hint}{suffix}")
        if len(lines) > 1:
            parts.append(_clamp_block("\n".join(lines), budget["relationships"]))

    if world_context.get("nearby_activity"):
        lines = ["[Nearby Activity]"]
        for item in world_context["nearby_activity"][:3]:
            lines.append(f"- {item.get('name', 'unknown')}: {item.get('summary', '')}")
        parts.append(_clamp_block("\n".join(lines), budget["location"]))

    if world_context.get("sns_posts"):
        lines = ["[SNS Feed]"]
        lines.extend(f"- {post}" for post in world_context["sns_posts"][:2])
        parts.append(_clamp_block("\n".join(lines), budget["recent_summary"]))

    if world_context.get("kakao_turn_context"):
        block = _render_kakao_turn_context(world_context["kakao_turn_context"])
        if block:
            parts.append(_clamp_block(block, budget["recent_summary"]))

    if world_context.get("kakao_rooms") and not world_context.get("kakao_turn_context"):
        lines = ["[KakaoTalk Rooms]"]
        for room in world_context["kakao_rooms"][:3]:
            room_name = room.get("room") or "톡방"
            members = ", ".join(room.get("members") or [])
            topic = room.get("topic") or ""
            header = f"- {room_name}"
            if members:
                header += f" ({members})"
            if topic:
                header += f": {topic}"
            lines.append(header)
            lines.extend(
                f"  - {message}"
                for message in (room.get("recent_messages") or [])[-4:]
                if message
            )
        parts.append(_clamp_block("\n".join(lines), budget["recent_summary"]))

    if world_context.get("static_events"):
        lines = ["[Upcoming Events]"]
        for event in world_context["static_events"][:3]:
            label = "active" if event.get("status") == "active" else "scheduled"
            lines.append(f"- [{label}] {event.get('hint', '')}")
        parts.append(_clamp_block("\n".join(lines), budget["rules"]))

    if world_context.get("routine_schedules"):
        lines = ["[Routine Schedules]"]
        for schedule in world_context["routine_schedules"][:6]:
            lines.append(_render_routine_schedule(schedule))
        lines.append("- This block is stable routine knowledge: use it to answer schedule questions, but do not assume the character is there unless it is today/current.")
        parts.append(_clamp_block("\n".join(lines), budget["schedules"]))

    if world_context.get("schedules"):
        lines = ["[Schedules]"]
        for schedule in world_context["schedules"][:4]:
            owner = schedule.get("owner_name") or schedule.get("owner_id") or "character"
            timing = schedule.get("timing") or "today"
            time_range = _schedule_time_range(schedule)
            name = schedule.get("name") or schedule.get("activity") or "schedule"
            hint = _first_present(schedule, ("prompt_hint", "summary", "activity")) or ""
            location = schedule.get("location_name") or schedule.get("location_id") or ""
            location_part = f" @ {location}" if location else ""
            material = _render_schedule_material(schedule.get("material"))
            realism = _render_schedule_realism(schedule)
            line = f"- [{timing}] {owner}: {time_range}{location_part} {name}. {hint}".strip()
            if realism:
                line += f" Timing: {realism}"
            if material:
                line += f" Material: {material}"
            lines.append(line)
        lines.append("- If a material includes lyric text, use only that lyric and do not invent or continue real lyrics.")
        parts.append(_clamp_block("\n".join(lines), budget["schedules"]))

    if world_context.get("life_goals"):
        lines = ["[Life Goals]"]
        for goal in world_context["life_goals"][:3]:
            title = goal.get("title") or "goal"
            hint = goal.get("hint") or goal.get("next_hint") or ""
            lines.append(f"- {title}: {hint}")
        parts.append(_clamp_block("\n".join(lines), budget["goals"]))

    if world_context.get("object_memories"):
        lines = ["[Object Memories]"]
        for item in world_context["object_memories"][:3]:
            name = item.get("name") or item.get("item_name") or item.get("item_id") or "item"
            hint = item.get("memory") or item.get("memory_summary") or item.get("summary") or item.get("hint") or ""
            lines.append(f"- {name}: {hint}")
        parts.append(_clamp_block("\n".join(lines), budget["items"]))

    if world_context.get("secret_hints"):
        lines = ["[Subtext]"]
        for secret in world_context["secret_hints"][:3]:
            title = secret.get("title") or "secret"
            hint = secret.get("hint") or secret.get("public_hint") or ""
            level = secret.get("reveal_level", secret.get("current_reveal_level", 0))
            lines.append(f"- {title} (reveal_level={level}): {hint}")
        parts.append(_clamp_block("\n".join(lines), budget["subtext"]))

    return "\n\n".join(part for part in parts if part)


def _render_kakao_turn_context(kakao_turn_context: dict) -> str:
    """Render this-turn KakaoTalk messages as context, not an Actor output format."""
    messages = kakao_turn_context.get("messages") if isinstance(kakao_turn_context, dict) else []
    if not messages:
        return ""
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
    return "\n".join(lines)


def _first_present(data: dict, keys: tuple[str, ...]) -> object:
    """Return the first non-empty value from a dict."""
    for key in keys:
        value = data.get(key)
        if value not in (None, "", []):
            return value
    return None


def _schedule_time_range(schedule: dict) -> str:
    """Render schedule start/end fields without empty punctuation."""
    start = schedule.get("start_time") or ""
    end = schedule.get("end_time") or ""
    if start and end:
        return f"{start}-{end}"
    return start or "today"


def _render_routine_schedule(schedule: dict) -> str:
    """Render minimal recurring schedule info without detailed material."""
    owner = schedule.get("owner_name") or schedule.get("owner_id") or "character"
    days = _weekday_label(schedule)
    time_range = _schedule_time_range(schedule)
    name = schedule.get("name") or schedule.get("activity") or "schedule"
    location = schedule.get("location_name") or schedule.get("location_id") or ""
    location_part = f" @ {location}" if location else ""
    today = " today" if schedule.get("is_today") else ""
    realism = _render_schedule_realism(schedule)
    line = f"- [{days}{today}] {owner}: {time_range}{location_part} {name}".strip()
    if realism:
        line += f" Timing: {realism}"
    return line


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


def _render_schedule_realism(schedule: dict) -> str:
    """Render optional schedule feasibility hints without forcing the scene."""
    parts = []
    prep = schedule.get("preparation_time_min")
    if prep not in (None, ""):
        parts.append(f"prep={prep}m")
    travel = schedule.get("travel_time_min")
    if travel not in (None, ""):
        parts.append(f"travel={travel}m")
    flexibility = schedule.get("flexibility")
    if flexibility:
        parts.append(f"flex={flexibility}")
    lateness = schedule.get("lateness_tolerance")
    if lateness:
        parts.append(f"late={lateness}")
    can_skip = _coerce_bool(schedule.get("can_skip"))
    if can_skip is not None:
        parts.append(f"can_skip={can_skip}")
    if schedule.get("requires_transition_scene"):
        parts.append("transition_required")
    return "; ".join(parts)


def _coerce_bool(value: object) -> bool | None:
    """Normalize bool-like schedule material values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return bool(value)


def _first_profile_hint(profiles: list[dict]) -> object:
    """Return the first usable prompt hint from profile-node rows."""
    for profile in profiles:
        hint = _first_present(profile, ("prompt_hint", "summary"))
        if hint:
            return hint
    return None


def _render_relationship_bands(relationship: dict) -> str:
    """Render relationship affinity/trust as coarse labels."""
    parts = []
    affinity = _as_float(relationship.get("affinity"))
    trust = _as_float(relationship.get("trust"))
    if affinity is not None:
        parts.append(f"affinity={_relationship_affinity_band(affinity)}")
    if trust is not None:
        parts.append(f"trust={_relationship_trust_band(trust)}")
    return ", ".join(parts)


def _relationship_affinity_band(value: float) -> str:
    """Map affinity to a relationship-distance band."""
    if value < 0:
        return "hostile"
    return _percent_band(value, "distant", "cautious", "comfortable", "intimate", "deeply bonded")


def _relationship_trust_band(value: float) -> str:
    """Map trust to a relationship-safety band."""
    if value < 0:
        return "distrustful"
    return _percent_band(value, "guarded", "uncertain", "trusting", "secure", "unwavering")


def _stress_band(value: float) -> str:
    """Map 0-10 stress-like values to expression bands."""
    normalized = value * 10 if 0 <= value <= 10 else _clamp_percent(value)
    if normalized <= 20:
        return "low"
    if normalized <= 50:
        return "managed"
    if normalized <= 70:
        return "high"
    return "overloaded"


def _mood_band(mood: str) -> str:
    """Map common mood labels to prompt-facing affect groups."""
    lowered = mood.lower()
    positive = {"calm", "happy", "excited", "relaxed", "content", "기쁨", "행복", "차분"}
    negative = {"sad", "angry", "annoyed", "anxious", "depressed", "화남", "불안", "우울", "짜증"}
    tired = {"tired", "exhausted", "sleepy", "피곤", "지침"}
    if lowered in positive:
        return "open"
    if lowered in negative:
        return "guarded"
    if lowered in tired:
        return "depleted"
    return "neutral"


def _need_band(value: float) -> str:
    """Map need values to urgency bands."""
    normalized = value * 100 if 0 <= value <= 1 else _clamp_percent(value)
    if normalized < 30:
        return "settled"
    if normalized < 60:
        return "noticeable"
    if normalized < 80:
        return "pressing"
    return "urgent"


def _percent_band(value: float, low: str, mid: str, high: str, very_high: str, maxed: str) -> str:
    """Map 0-100-ish values to five stable bands."""
    normalized = _clamp_percent(value)
    if normalized <= 30:
        return low
    if normalized <= 60:
        return mid
    if normalized <= 80:
        return high
    if normalized <= 95:
        return very_high
    return maxed


def _first_numeric(data: dict, keys: tuple[str, ...]) -> float | None:
    """Return the first parseable numeric value for one of the given keys."""
    for key in keys:
        value = _as_float(data.get(key))
        if value is not None:
            return value
    return None


def _as_float(value: object) -> float | None:
    """Coerce numeric DB values without treating blanks as zero."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_percent(value: float) -> float:
    """Clamp a raw 0-100 style value."""
    return max(0.0, min(100.0, value))


def _format_number(value: float) -> str:
    """Format numeric bands without noisy trailing decimals."""
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _compact_json(data: dict, limit: int) -> str:
    """Compact unknown dicts for fallback rendering."""
    return _clamp_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), limit)


def _clamp_block(text: str, limit: int) -> str:
    """Clamp a rendered block to an approximate character budget."""
    return _clamp_text(text.strip(), max(80, int(limit)))


def _clamp_text(text: str, limit: int) -> str:
    """Trim text at a line boundary when possible."""
    if len(text) <= limit:
        return text
    clipped = text[: max(0, limit - 20)]
    boundary = clipped.rfind("\n")
    if boundary > limit * 0.5:
        clipped = clipped[:boundary]
    return clipped.rstrip() + "\n- ..."
