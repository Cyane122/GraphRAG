# ================================
# src/agents/manager/prompting.py
#
# Manager prompt rendering stage helpers.
#
# Functions
#   - resolve_prompt_world_config(world: World, world_config: dict, npc_id: str, pc_id: str, perspective: int) -> dict : Resolve prompt world config
#   - build_prompt_parts(user_input: str, recent_story: str, perspective: int, world_config: dict, scene_plan: SceneTimePlan, context: CoreContext, world_context: dict, scene_need_hints: dict[str, str] | None, turn_ooc_directives: str = "") -> PromptParts : Render manager prompt parts
# ================================

from src.agents.context.renderer import build_rendered_dynamic_context
from src.agents.manager.models import CoreContext, PromptParts, SceneTimePlan
from src.agents.manager.pov import build_current_pov_context
from src.agents.prompt_factory.builder import PromptBuilder
from src.assets.worlds.base import World
from src.core.database import async_driver


async def resolve_prompt_world_config(
    world: World,
    world_config: dict,
    npc_id: str,
    pc_id: str,
    perspective: int,
) -> dict:
    """Load async world config when available, otherwise keep the cached config."""
    if hasattr(world, "get_full_config_async"):
        return await world.get_full_config_async([npc_id, pc_id], async_driver)
    return world_config or world.get_full_config(perspective)


def build_prompt_parts(
    user_input: str,
    recent_story: str,
    perspective: int,
    world_config: dict,
    scene_plan: SceneTimePlan,
    context: CoreContext,
    world_context: dict,
    scene_need_hints: dict[str, str] | None = None,
    turn_ooc_directives: str = "",
) -> PromptParts:
    """Render Fixed, Genre, and Dynamic prompt segments from prepared context."""
    recall_events = _format_recall_events_for_prompt(context)
    current_pov = build_current_pov_context(
        context,
        world_config,
    )
    builder = PromptBuilder(
        world_config,
        context.char_data.get("name"),
        context.user_data.get("name"),
        perspective=perspective,
    )
    fixed_prompt, genre_prompt, dynamic_prompt = builder.build(
        scene_types=scene_plan.scene_types,
        char_data=context.char_data,
        user_data=context.user_data,
        recent_story=recent_story,
        user_input=user_input,
        location=context.location_name,
        location_nodes=context.location_nodes,
        npcs=context.active_npcs,
        dt=scene_plan.current_dt,
        current_pov=current_pov,
        scene_need_hints=scene_need_hints or {},
        rendered_context=build_rendered_dynamic_context(
            scene_state=context.scene_state,
            context_plan=context.context_plan,
            relationship=context.relationship,
            events=context.recent_events,
            recall_events=recall_events,
            personal_facts=context.personal_facts,
            npcs=context.active_npcs,
            world_context=world_context,
            dynamic_state=context.char_data.get("dynamic_state", {}),
        ),
        turn_ooc_directives=turn_ooc_directives,
    )
    return PromptParts(fixed=fixed_prompt, genre=genre_prompt, dynamic=dynamic_prompt)


def _format_recall_events_for_prompt(context: CoreContext) -> list[dict]:
    """Mark recalled memories that may conflict with the NPC's distorted memory."""
    conflicted_ids = {
        memory["id"]
        for memory in context.raw_memories
        if float(memory.get("distortion") or 0) > 0.2
    }
    return [
        {
            "summary": event["summary"],
            "score": event.get("score", 0),
            "memory_type": event.get("memory_type"),
            "conflict": event["id"] in conflicted_ids,
        }
        for event in context.recall_events
    ]
