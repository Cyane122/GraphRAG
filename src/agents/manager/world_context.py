# ================================
# src/agents/manager/world_context.py
#
# Collect dynamic world-context hints for the Manager pipeline.
#
# Functions
#   - fetch_dynamic_world_context(npc_id: str, pc_id: str, location_id: str | None, current_dt: datetime, user_input: str, context_plan: dict, recent_story: str = "", preloaded_schedule_context: dict | None = None, social_media_features: dict | None = None) -> dict : Fetch prompt-ready world context
#   - _wants_kakao_context(user_input: str, required_systems: set[str]) -> bool : Decide whether to fetch KakaoTalk context.
#   - _prompt_safe_secret_hints(secret_hints: list[dict]) -> list[dict] : Strip Secret hints to prompt-safe public fields.
#   - _merge_rules(existing_rules: list[dict], extra_rules: list[dict]) -> list[dict] : Merge rule lists by id.
#   - _context_importance(context_plan: dict) -> int : Safely read context planner importance.
# ================================
from datetime import datetime

from src.agents.context.generic import fetch_generic_prompt_context
from src.simulation.events.manager import evaluate_all as evaluate_static_events
from src.simulation.systems.scheduling.schedules import SCHEDULE_PROMPT_WINDOW_MIN, fetch_schedule_context
from src.simulation.systems.scheduling.time_rules import fetch_time_rule_context
from src.simulation.systems.social import build_world_context
from src.simulation.systems.kakao import fetch_kakao_context

async def fetch_dynamic_world_context(
    npc_id: str,
    pc_id: str,
    location_id: str | None,
    current_dt: datetime,
    user_input: str,
    context_plan: dict,
    recent_story: str = "",
    preloaded_schedule_context: dict | None = None,
    social_media_features: dict | None = None,
) -> dict:
    """Collect optional dynamic world hints used by the prompt renderer."""
    required_systems = set(context_plan.get("required_systems", []))
    features = social_media_features or {}
    kakao_enabled = bool(features.get("kakao_enabled", False))
    instagram_enabled = bool(features.get("instagram_enabled", False))
    world_context: dict = {}
    try:
        generic_context = await fetch_generic_prompt_context(
            npc_id=npc_id,
            pc_id=pc_id,
            location_id=location_id,
            scene_type=context_plan.get("scene_type", "daily"),
            user_input=f"{recent_story}\n{user_input}",
        )
        world_context.update({key: value for key, value in generic_context.items() if value})
    except Exception as e:
        print(f"[GenericContext] prompt node fetch failed (ignored): {e}")

    if "social" in required_systems:
        try:
            social_context = await build_world_context(
                npc_id       = npc_id,
                pc_id        = pc_id,
                location_id  = location_id or "",
                current_time = current_dt,
                enable_sns   = instagram_enabled,
            )
            world_context.update({key: value for key, value in social_context.items() if value})
        except Exception as e:
            print(f"[WorldNarrator] 컨텍스트 수집 실패 (무시): {e}")

    if kakao_enabled and _wants_kakao_context(user_input, required_systems):
        try:
            kakao_rooms = await fetch_kakao_context(pc_id=pc_id)
            if kakao_rooms:
                world_context["kakao_rooms"] = kakao_rooms
        except Exception as e:
            print(f"[Kakao] context fetch failed (ignored): {e}")

    if "social" in required_systems or "goals" in required_systems:
        try:
            static_hints = await evaluate_static_events(current_dt, commit=False)
            if static_hints:
                world_context["static_events"] = static_hints
        except Exception as e:
            print(f"[StaticEvent] 평가 실패 (무시): {e}")

    await _attach_life_depth_hints(
        world_context,
        npc_id,
        pc_id,
        location_id,
        current_dt,
        user_input,
        required_systems,
        context_importance=_context_importance(context_plan),
        preloaded_schedule_context=preloaded_schedule_context,
    )

    try:
        from src.simulation.systems.memory.narrative import fetch_narrative_log
        narrative_log = await fetch_narrative_log()
        if narrative_log:
            world_context["narrative_log"] = narrative_log
    except Exception as e:
        print(f"[NarrativeLog] fetch 실패 (무시): {e}")

    return world_context


def _wants_kakao_context(user_input: str, required_systems: set[str]) -> bool:
    """Return True when the turn likely needs KakaoTalk room context."""
    lowered = user_input.lower()
    if any(token in lowered for token in ("카톡", "카카오", "톡방", "문자", "메시지", "message", "texting")):
        return True
    return "social" in required_systems


async def _attach_life_depth_hints(
    world_context: dict,
    npc_id: str,
    pc_id: str,
    location_id: str | None,
    current_dt: datetime,
    user_input: str,
    required_systems: set[str],
    context_importance: int = 0,
    preloaded_schedule_context: dict | None = None,
) -> None:
    """Attach optional schedule, goal, item, and secret hints to dynamic context."""
    try:
        schedule_context = preloaded_schedule_context or await fetch_schedule_context(
            current_time=current_dt,
            window_minutes=SCHEDULE_PROMPT_WINDOW_MIN,
        )
        world_context.update({key: value for key, value in schedule_context.items() if value})
    except Exception as e:
        print(f"[Schedule] hints failed (ignored): {e}")

    try:
        time_rule_context = await fetch_time_rule_context(
            current_time=current_dt,
            location_id=location_id,
        )
        time_rules = time_rule_context.get("time_rules") or []
        if time_rules:
            world_context["time_rules"] = time_rules
            world_context["rules"] = _merge_rules(world_context.get("rules") or [], time_rules)
    except Exception as e:
        print(f"[TimeRules] hints failed (ignored): {e}")

    high_importance = context_importance >= 6

    if "goals" in required_systems or high_importance:
        try:
            from src.simulation.systems.goals import fetch_goal_hints

            goal_hints = await fetch_goal_hints(
                owner_id     = npc_id,
                pc_id        = pc_id,
                current_time = current_dt,
                limit        = 2,
            )
            if goal_hints:
                world_context["life_goals"] = goal_hints
        except Exception as e:
            print(f"[LifeDepth] goal hints failed (ignored): {e}")

    if "items" in required_systems:
        try:
            from src.simulation.systems.items import fetch_object_memory_hints

            item_hints = await fetch_object_memory_hints(
                owner_id    = npc_id,
                pc_id       = pc_id,
                location_id = location_id or "",
                user_input  = user_input,
                limit       = 2,
            )
            if item_hints:
                world_context["object_memories"] = item_hints
        except Exception as e:
            print(f"[LifeDepth] object hints failed (ignored): {e}")

    if "secrets" in required_systems or high_importance:
        try:
            from src.simulation.systems.secrets import fetch_secret_hints

            secret_hints = await fetch_secret_hints(
                owner_id     = npc_id,
                pc_id        = pc_id,
                current_time = current_dt,
                limit        = 2,
            )
            if secret_hints:
                world_context["secret_hints"] = _prompt_safe_secret_hints(secret_hints)
        except Exception as e:
            print(f"[LifeDepth] secret hints failed (ignored): {e}")


def _prompt_safe_secret_hints(secret_hints: list[dict]) -> list[dict]:
    """Keep only prompt-safe public Secret fields."""
    safe_hints = []
    for hint in secret_hints:
        public_hint = hint.get("hint") or hint.get("public_hint") or ""
        if not public_hint:
            continue
        safe_hints.append({
            "id": hint.get("id", ""),
            "owner_id": hint.get("owner_id", ""),
            "title": hint.get("title", ""),
            "hint": public_hint,
            "public_hint": public_hint,
            "status": hint.get("status", ""),
            "reveal_level": hint.get("reveal_level", hint.get("current_reveal_level", 0)),
        })
    return safe_hints


def _merge_rules(existing_rules: list[dict], extra_rules: list[dict]) -> list[dict]:
    """Merge prompt Rule lists by id while preserving priority order."""
    merged: dict[str, dict] = {}
    for rule in [*existing_rules, *extra_rules]:
        rule_id = str(rule.get("id") or len(merged))
        merged[rule_id] = rule
    return sorted(
        merged.values(),
        key=lambda rule: int(rule.get("prompt_priority") or 0),
        reverse=True,
    )


def _context_importance(context_plan: dict) -> int:
    """Read planner importance without letting malformed values break prompt."""
    try:
        return int(context_plan.get("importance") or 0)
    except (TypeError, ValueError):
        return 0
