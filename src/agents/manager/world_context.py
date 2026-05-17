# ================================
# src/agents/manager/world_context.py
#
# Collect dynamic world-context hints for the Manager pipeline.
#
# Functions
#   - fetch_dynamic_world_context(npc_id: str, pc_id: str, location_id: str | None, current_dt: datetime, user_input: str, context_plan: dict, recent_story: str = "") -> dict : Fetch prompt-ready world context
# ================================
from datetime import datetime

from src.agents.context.generic import fetch_generic_prompt_context
from src.simulation.events.manager import evaluate_all as evaluate_static_events
from src.simulation.systems.schedules import fetch_schedule_context
from src.simulation.systems.social import build_world_context

async def fetch_dynamic_world_context(
    npc_id: str,
    pc_id: str,
    location_id: str | None,
    current_dt: datetime,
    user_input: str,
    context_plan: dict,
    recent_story: str = "",
) -> dict:
    """Collect optional dynamic world hints used by the prompt renderer."""
    required_systems = set(context_plan.get("required_systems", []))
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
            )
            world_context.update({key: value for key, value in social_context.items() if value})
        except Exception as e:
            print(f"[WorldNarrator] 컨텍스트 수집 실패 (무시): {e}")

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
    )

    try:
        from src.simulation.systems.memory.narrative import fetch_narrative_log
        narrative_log = await fetch_narrative_log()
        if narrative_log:
            world_context["narrative_log"] = narrative_log
    except Exception as e:
        print(f"[NarrativeLog] fetch 실패 (무시): {e}")

    return world_context


async def _attach_life_depth_hints(
    world_context: dict,
    npc_id: str,
    pc_id: str,
    location_id: str | None,
    current_dt: datetime,
    user_input: str,
    required_systems: set[str],
) -> None:
    """Attach optional schedule, goal, item, and secret hints to dynamic context."""
    try:
        schedule_context = await fetch_schedule_context(
            current_time=current_dt,
            window_minutes=120,
        )
        world_context.update({key: value for key, value in schedule_context.items() if value})
    except Exception as e:
        print(f"[Schedule] hints failed (ignored): {e}")

    if "goals" in required_systems:
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

    if "secrets" in required_systems:
        try:
            from src.simulation.systems.secrets import fetch_secret_hints

            secret_hints = await fetch_secret_hints(
                owner_id     = npc_id,
                pc_id        = pc_id,
                current_time = current_dt,
                limit        = 2,
            )
            if secret_hints:
                world_context["secret_hints"] = secret_hints
        except Exception as e:
            print(f"[LifeDepth] secret hints failed (ignored): {e}")
