# ================================
# src/agents/manager/planning.py
#
# Manager bootstrap and scene/time planning helpers.
#
# Functions
#   - bootstrap_manager(world_id: str | None, perspective: int, deps: ManagerDependencies) -> ManagerBootstrap : Load world and global state
#   - classify_scene_and_time(user_input: str, recent_story: str, bootstrap: ManagerBootstrap, pc_id: str, npc_id: str, suppress_time_plan: bool, deps: ManagerDependencies) -> SceneTimePlan : Build scene and time plan
#   - _fetch_time_parser_schedule_context(bootstrap: ManagerBootstrap) -> dict : Fetch schedule hints for time parsing
# ================================

from datetime import datetime

from src.agents.context.scene_keys import normalize_scene_types
from src.agents.manager.models import ManagerBootstrap, ManagerDependencies, SceneTimePlan
from src.assets.worlds.base import World
from src.simulation.systems.schedules import fetch_schedule_context
from src.simulation.state.updater import build_time_plan


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
    parse_result, scene_types = await _classify_scene(user_input, recent_story, bootstrap, deps)
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
    manager_effects = build_manager_effects(time_plan, bootstrap.global_state, pc_id, npc_id)
    return SceneTimePlan(
        parse_result=parse_result,
        scene_types=scene_types,
        base_time=base_time,
        current_dt=current_dt,
        time_plan=time_plan,
        manager_effects=manager_effects,
    )


def build_manager_effects(
    time_plan: dict,
    global_state: dict,
    pc_id: str,
    npc_id: str,
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

    return {
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


async def _classify_scene(
    user_input: str,
    recent_story: str,
    bootstrap: ManagerBootstrap,
    deps: ManagerDependencies,
) -> tuple[dict, list[str]]:
    """Classify a scene via rules first, then the LLM classifier."""
    rule_result = deps.try_rule_based(user_input, recent_story)
    if rule_result:
        scene_types = rule_result["scene_types"]
        print(f"[Classify+Time / rule-based] scene={scene_types} elapsed={rule_result['elapsed_minutes']}min")
        return rule_result, scene_types

    allowed_locs = await deps.get_allowed_locations()
    schedule_context = await _fetch_time_parser_schedule_context(bootstrap)
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
    """Fetch current schedule pressure for Manager time parsing."""
    try:
        current_time = datetime.fromisoformat(bootstrap.global_state["currentTime"])
        return await fetch_schedule_context(current_time=current_time, window_minutes=360)
    except Exception as e:
        print(f"[Schedule] time parser hints failed (ignored): {e}")
        return {}
