# ================================
# src/agents/manager/planning.py
#
# Manager bootstrap and scene planning helpers.
#
# Functions
#   - bootstrap_manager(world_id: str | None, perspective: int, deps: ManagerDependencies) -> ManagerBootstrap : Load world and global state
#   - classify_scene_and_time(user_input: str, recent_story: str, bootstrap: ManagerBootstrap, pc_id: str, npc_id: str, suppress_time_plan: bool, deps: ManagerDependencies) -> SceneTimePlan : Build scene plan with DB time as the prompt baseline
#   - _fetch_time_parser_schedule_context(bootstrap: ManagerBootstrap) -> dict : Fetch schedule and time-rule hints for prompt context
#   - _build_static_time_plan(base_time: datetime) -> dict : Build a non-mutating baseline time plan
# ================================

from datetime import datetime

from src.agents.context.scene_keys import normalize_scene_types
from src.agents.manager.models import ManagerBootstrap, ManagerDependencies, SceneTimePlan
from src.assets.worlds.base import World
from src.simulation.systems.scheduling.schedules import SCHEDULE_TIME_PARSE_WINDOW_MIN, fetch_schedule_context
from src.simulation.systems.scheduling.time_rules import fetch_time_rule_context


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
    """Classify the scene without LLM time parsing and keep DB time as the baseline."""
    schedule_context = await _fetch_time_parser_schedule_context(bootstrap)
    parse_result, scene_types = await _classify_scene(
        user_input,
        recent_story,
        bootstrap,
        deps,
    )
    scene_types = normalize_scene_types(scene_types)
    base_time = datetime.fromisoformat(bootstrap.global_state["currentTime"])
    del suppress_time_plan
    time_plan = _build_static_time_plan(base_time)
    current_dt = base_time
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
    """Build non-time manager effects while keeping planning side-effect free."""
    pending_effects = []
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

    effects = {
        "time_plan": None,
        "pending_effects": pending_effects,
    }
    if schedule_context:
        effects["schedule_context"] = schedule_context
    if time_plan:
        effects["prompt_time_baseline"] = time_plan
    return effects


async def _classify_scene(
    user_input: str,
    recent_story: str,
    bootstrap: ManagerBootstrap,
    deps: ManagerDependencies,
) -> tuple[dict, list[str]]:
    """Classify a scene with rule shortcuts and scene-only LLM fallback."""
    rule_result = deps.try_rule_based(user_input, recent_story)
    if rule_result:
        scene_types = rule_result.get("scene_types") or ["daily"]
        if scene_types != ["daily"]:
            print(f"[Scene / rule-based] scene={scene_types}")
            return rule_result, scene_types
    scene_result = await deps.classify_scene_only(
        user_input,
        recent_story,
        bootstrap.world.get_scene_descriptions(),
    )
    return scene_result, scene_result.get("scene_types") or ["daily"]


def _build_static_time_plan(base_time: datetime) -> dict:
    """Build a baseline time plan that never mutates DB time."""
    return {
        "action_type": "actor_header_pending",
        "base_time": base_time.isoformat(),
        "new_time": base_time.isoformat(),
        "elapsed_minutes": 0.0,
        "days_passed": 0,
        "new_weather": None,
        "new_location_id": None,
        "new_location": None,
        "reason": "DB time is committed later from the accepted Actor prose header.",
    }


async def _fetch_time_parser_schedule_context(bootstrap: ManagerBootstrap) -> dict:
    """Fetch current schedule pressure and time rules for prompt context."""
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
