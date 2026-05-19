# ================================
# src/agents/manager/planning.py
#
# Manager bootstrap and scene/time planning helpers.
#
# Functions
#   - bootstrap_manager(world_id: str | None, perspective: int, deps: ManagerDependencies) -> ManagerBootstrap : Load world and global state
#   - classify_scene_and_time(user_input: str, recent_story: str, bootstrap: ManagerBootstrap, pc_id: str, npc_id: str, suppress_time_plan: bool, deps: ManagerDependencies) -> SceneTimePlan : Build scene and time plan
#   - _fetch_time_parser_schedule_context(bootstrap: ManagerBootstrap) -> dict : Fetch schedule and time-rule hints for time parsing
#   - _has_schedule_pressure(schedule_context: dict) -> bool : Detect active or upcoming same-day schedules
#   - _is_schedule_sensitive_rule_result(rule_result: dict, user_input: str) -> bool : Decide whether schedule should bypass rule-based parsing
#   - _build_schedule_conflicts(time_plan: dict, schedule_context: dict) -> list[dict] : Build deterministic schedule conflict signals
#   - _safe_float(value: object, default: float | None = 0.0) -> float | None : Coerce schedule numeric fields
# ================================

import re
from datetime import datetime

from src.agents.context.scene_keys import normalize_scene_types
from src.agents.manager.models import ManagerBootstrap, ManagerDependencies, SceneTimePlan
from src.assets.worlds.base import World
from src.simulation.systems.schedules import SCHEDULE_TIME_PARSE_WINDOW_MIN, fetch_schedule_context
from src.simulation.systems.time_rules import fetch_time_rule_context
from src.simulation.state.updater import build_time_plan

_SCHEDULE_SENSITIVE_INPUT_RE = re.compile(
    r"(가자|갈까|나가|출발|이동|학교|수업|강의|회사|직장|약속|일정|준비|늦|"
    r"go|leave|move|school|class|work|appointment|schedule|ready|late)",
    re.IGNORECASE,
)


async def bootstrap_manager(
    world_id: str | None,
    perspective: int,
    deps: ManagerDependencies,
) -> ManagerBootstrap:
    """Load the active world, its fixed config, and current global state."""
    world: World = deps.load_world_instance(world_id)
    world_config = world.get_full_config(perspective)
    start_dt = world_config.get("start_time")
    global_state = await deps.fetch_global_state(start_dt)
    return ManagerBootstrap(world=world, world_config=world_config, global_state=global_state)


async def classify_scene_and_time(
    user_input: str,
    recent_story: str,
    bootstrap: ManagerBootstrap,
    pc_id: str,
    npc_id: str,
    suppress_time_plan: bool,
    deps: ManagerDependencies,
) -> SceneTimePlan:
    """Classify the scene and build a pending time/effect plan without DB writes."""
    schedule_context = await _fetch_time_parser_schedule_context(bootstrap)
    parse_result, scene_types = await _classify_scene(
        user_input,
        recent_story,
        bootstrap,
        deps,
        schedule_context,
    )
    scene_types = normalize_scene_types(scene_types)
    base_time = datetime.fromisoformat(bootstrap.global_state["currentTime"])
    if suppress_time_plan:
        time_plan = {
            "action_type": "ooc_preapplied",
            "base_time": base_time.isoformat(),
            "new_time": base_time.isoformat(),
            "elapsed_minutes": 0.0,
            "days_passed": 0,
            "new_weather": None,
            "new_location_id": None,
            "reason": "OOC time patch already applied before manager planning.",
        }
    else:
        time_plan = build_time_plan(parse_result, base_time)
    current_dt = datetime.fromisoformat(time_plan["new_time"])
    manager_effects = build_manager_effects(time_plan, bootstrap.global_state, pc_id, npc_id, schedule_context)
    return SceneTimePlan(
        parse_result=parse_result,
        scene_types=scene_types,
        base_time=base_time,
        current_dt=current_dt,
        time_plan=time_plan,
        manager_effects=manager_effects,
        schedule_context=schedule_context,
    )


def build_manager_effects(
    time_plan: dict,
    global_state: dict,
    pc_id: str,
    npc_id: str,
    schedule_context: dict | None = None,
) -> dict:
    """Build commit-time manager effects while keeping planning side-effect free."""
    pending_effects = [
        {
            "type": "global_time_update",
            "target": "GlobalState:singleton",
            "field": "currentTime",
            "old_value": time_plan["base_time"],
            "new_value": time_plan["new_time"],
        }
    ]
    if time_plan.get("new_weather"):
        pending_effects.append({
            "type": "global_weather_update",
            "target": "GlobalState:singleton",
            "field": "weather",
            "old_value": global_state.get("weather"),
            "new_value": time_plan["new_weather"],
        })
    if time_plan.get("new_location_id"):
        pending_effects.append({
            "type": "location_update",
            "target": f"Character:{pc_id},{npc_id}",
            "field": "LOCATED_AT",
            "old_value": global_state.get("currentLocationId"),
            "new_value": time_plan["new_location_id"],
        })

    schedule_conflicts = _build_schedule_conflicts(time_plan, schedule_context or {})
    effects = {
        "time_plan": time_plan,
        "pending_effects": pending_effects,
        "needs_update": {
            "pc_id": pc_id,
            "elapsed_minutes": time_plan["elapsed_minutes"],
            "current_time": time_plan["new_time"],
        },
        "daily_systems": {
            "days_passed": time_plan["days_passed"],
            "current_time": time_plan["new_time"],
        },
    }
    if schedule_context:
        effects["schedule_context"] = schedule_context
    if schedule_conflicts:
        effects["schedule_conflicts"] = schedule_conflicts
        pending_effects.extend(
            {
                "type": "schedule_conflict",
                "target": f"Schedule:{conflict.get('schedule_id')}",
                "field": conflict.get("field", "elapsed_minutes"),
                "old_value": conflict.get("limit"),
                "new_value": conflict.get("value"),
            }
            for conflict in schedule_conflicts
        )
    return effects


async def _classify_scene(
    user_input: str,
    recent_story: str,
    bootstrap: ManagerBootstrap,
    deps: ManagerDependencies,
    schedule_context: dict,
) -> tuple[dict, list[str]]:
    """Classify a scene via rules first, then the LLM classifier."""
    _STANDARD_SCENE_TYPES = {"daily", "emotional", "physical", "intimate", "workplace", "aegyo"}
    _world_has_custom_types = bool(set(bootstrap.world.get_scene_types()) - _STANDARD_SCENE_TYPES)

    rule_result = deps.try_rule_based(user_input, recent_story)
    if rule_result and not _is_schedule_sensitive_rule_result(rule_result, user_input, schedule_context):
        scene_types = rule_result["scene_types"]
        # 월드에 커스텀 씬 타입이 있으면 rule-based daily 결과는 LLM으로 위임
        if _world_has_custom_types and scene_types == ["daily"]:
            pass
        else:
            print(f"[Classify+Time / rule-based] scene={scene_types} elapsed={rule_result['elapsed_minutes']}min")
            return rule_result, scene_types

    allowed_locs = await deps.get_allowed_locations()
    parse_result = await deps.classify_and_parse_time(
        user_input,
        recent_story,
        bootstrap.global_state,
        allowed_locs,
        bootstrap.world.get_scene_descriptions(),
        schedule_context,
    )
    return parse_result, parse_result.get("scene_types") or ["daily"]


async def _fetch_time_parser_schedule_context(bootstrap: ManagerBootstrap) -> dict:
    """Fetch current schedule pressure and time rules for Manager time parsing."""
    try:
        current_time = datetime.fromisoformat(bootstrap.global_state["currentTime"])
        schedule_context = await fetch_schedule_context(
            current_time=current_time,
            window_minutes=SCHEDULE_TIME_PARSE_WINDOW_MIN,
        )
        time_rule_context = await fetch_time_rule_context(
            current_time=current_time,
            location_id=bootstrap.global_state.get("currentLocationId"),
        )
        schedule_context.update({key: value for key, value in time_rule_context.items() if value})
        return schedule_context
    except Exception as e:
        print(f"[Schedule] time parser hints failed (ignored): {e}")
        return {}


def _has_schedule_pressure(schedule_context: dict) -> bool:
    """Return True when same-day schedule hints can constrain the current turn."""
    return any(
        schedule.get("timing") in {"active", "upcoming"}
        for schedule in schedule_context.get("schedules") or []
    )


def _is_schedule_sensitive_rule_result(
    rule_result: dict,
    user_input: str,
    schedule_context: dict,
) -> bool:
    """Decide whether a rule-based parse should defer to the LLM schedule-aware parser."""
    has_time_rules = bool(schedule_context.get("time_rules"))
    if not _has_schedule_pressure(schedule_context) and not has_time_rules:
        return False
    if rule_result.get("action_type") in {"movement", "ooc_jump"}:
        return True
    return bool(_SCHEDULE_SENSITIVE_INPUT_RE.search(user_input))


def _build_schedule_conflicts(time_plan: dict, schedule_context: dict) -> list[dict]:
    """Build deterministic schedule pressure signals after the time plan is computed."""
    try:
        elapsed = float(time_plan.get("elapsed_minutes") or 0)
    except (TypeError, ValueError):
        elapsed = 0.0
    new_location_id = time_plan.get("new_location_id")
    conflicts: list[dict] = []

    for schedule in schedule_context.get("schedules") or []:
        timing = schedule.get("timing")
        if timing not in {"active", "upcoming"}:
            continue
        minutes_until = _safe_float(schedule.get("minutes_until"), None)
        prep = _safe_float(schedule.get("preparation_time_min"), 0.0)
        travel = _safe_float(schedule.get("travel_time_min"), 0.0)
        required_buffer = prep + travel
        if timing == "upcoming" and minutes_until is not None and elapsed + required_buffer > minutes_until:
            conflicts.append({
                "schedule_id": schedule.get("id"),
                "owner_id": schedule.get("owner_id"),
                "name": schedule.get("name") or schedule.get("activity"),
                "field": "elapsed_minutes",
                "limit": minutes_until,
                "value": elapsed + required_buffer,
                "reason": "planned elapsed time plus preparation/travel overlaps an upcoming schedule",
            })
        schedule_location_id = schedule.get("location_id")
        if new_location_id and schedule_location_id and new_location_id != schedule_location_id and timing == "active":
            conflicts.append({
                "schedule_id": schedule.get("id"),
                "owner_id": schedule.get("owner_id"),
                "name": schedule.get("name") or schedule.get("activity"),
                "field": "new_location_id",
                "limit": schedule_location_id,
                "value": new_location_id,
                "reason": "planned movement conflicts with an active schedule location",
            })

    return conflicts


def _safe_float(value: object, default: float | None = 0.0) -> float | None:
    """Coerce numeric schedule fields while preserving caller-selected defaults."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
