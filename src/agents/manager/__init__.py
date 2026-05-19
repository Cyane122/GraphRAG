# ================================
# src/agents/manager/__init__.py
#
# Manager public API and execution entry point.
#
# Functions
#   - load_world_instance(world_id: str) -> World : Load the World instance for a world id
#   - run_manager(user_input: str, pc_id: str, npc_id: str, recent_story: str, world_id: str | None, scenario_id: str | None, perspective: int, return_meta: bool = False, suppress_time_plan: bool = False, scene_need_hints: dict[str, str] | None = None) -> tuple : Run one manager turn pipeline
#   - commit_manager_effects(effects: dict | None, pc_id: str, npc_id: str) -> None : Commit pending manager side effects
# ================================
import asyncio

from src.agents.manager.classifier import _classify_and_parse_time, _try_rule_based
from src.agents.manager.effects import commit_manager_effects
from src.agents.manager.pipeline import run_manager_pipeline
from src.agents.manager.models import ManagerDependencies
from src.agents.manager.queries import (
    _get_allowed_locations,
    detect_present_npcs,
    fetch_character_data,
    fetch_global_state,
    fetch_location,
    fetch_location_character_ids,
    fetch_location_hierarchy,
    fetch_npc_profiles,
    fetch_recent_events,
    fetch_relationship_data,
    get_location_name_from_id,
)
from src.agents.manager.world_loader import load_world_instance

async def run_manager(
    user_input:   str,
    pc_id:        str,
    npc_id:       str,
    recent_story: str = "",
    world_id:     str = None,
    scenario_id:  str | None = None,
    perspective:  int = 3,
    return_meta:   bool = False,
    suppress_time_plan: bool = False,
    scene_need_hints: dict[str, str] | None = None,
) -> tuple[str, str, str, list[str]] | tuple[str, str, str, list[str], dict]:
    """Orchestrate turn preparation while leaving each stage testable in isolation."""
    prompts, scene_types, manager_effects = await run_manager_pipeline(
        user_input,
        pc_id,
        npc_id,
        recent_story,
        world_id,
        perspective,
        suppress_time_plan,
        ManagerDependencies(
            load_world_instance=lambda wid: load_world_instance(wid, scenario_id),
            fetch_global_state=fetch_global_state,
            try_rule_based=_try_rule_based,
            get_allowed_locations=_get_allowed_locations,
            classify_and_parse_time=_classify_and_parse_time,
            fetch_character_data=fetch_character_data,
            fetch_relationship_data=fetch_relationship_data,
            fetch_recent_events=fetch_recent_events,
            get_location_name_from_id=get_location_name_from_id,
            fetch_location=fetch_location,
            fetch_location_hierarchy=fetch_location_hierarchy,
            detect_present_npcs=detect_present_npcs,
            fetch_location_character_ids=fetch_location_character_ids,
            fetch_npc_profiles=fetch_npc_profiles,
        ),
        scene_need_hints=scene_need_hints,
    )

    if return_meta:
        return (
            prompts.fixed,
            prompts.genre,
            prompts.dynamic,
            scene_types,
            manager_effects,
        )
    return prompts.fixed, prompts.genre, prompts.dynamic, scene_types

# ════════════════════════════════════════════════════════════
# 테스트
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def _test():
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input   = "*지희와 아린이 놀러 왔다. 은서와 셋이 소파에 앉아 수다를 떤다.*",
            pc_id        = "sian",
            npc_id       = "eun_seo",
            recent_story = "토요일 오후, 은서네 집.",
            world_id     = "babe_univ",
            perspective  = 3,
        )
        print("=== FIXED ===");   print(fixed[:200],  "...\n")
        print("=== GENRE ===");   print(genre[:200] if genre else "(없음)", "\n")
        print("=== DYNAMIC ==="); print(dynamic)
        print("\n=== 씬 타입 ==="); print(scene_types)

    asyncio.run(_test())
