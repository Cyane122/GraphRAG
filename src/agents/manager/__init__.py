# ================================
# src/agents/manager/__init__.py
#
# Manager public API and execution entry point.
#
# Functions
#   - load_world_instance(world_id: str) -> World : Load the World instance for a world id
#   - run_manager(user_input: str, pc_id: str, npc_id: str, recent_story: str, world_id: str | None, scenario_id: str | None, perspective: int, suppress_time_plan: bool = False, scene_need_hints: dict[str, str] | None = None, pending_kakao_messages: list[dict] | None = None, enable_kakao_preprocessing: bool = True, social_media_features: dict | None = None, thread_id: str | None = None, commit_id: str | None = None, turn_ooc_directives: str = "") -> tuple[PromptParts, list[str], dict] : Run one manager turn pipeline
#   - commit_manager_effects(effects: dict | None, pc_id: str, npc_id: str) -> None : Commit pending manager side effects
# ================================
import asyncio

from src.agents.manager.effects import commit_manager_effects
from src.agents.manager.models import PromptParts
from src.agents.manager.pipeline import run_manager_pipeline
from src.agents.manager.world_loader import load_world_instance

async def run_manager(
    user_input:   str,
    pc_id:        str,
    npc_id:       str,
    recent_story: str = "",
    world_id:     str | None = None,
    scenario_id:  str | None = None,
    perspective:  int = 3,
    suppress_time_plan: bool = False,
    scene_need_hints: dict[str, str] | None = None,
    pending_kakao_messages: list[dict] | None = None,
    enable_kakao_preprocessing: bool = True,
    social_media_features: dict | None = None,
    thread_id: str | None = None,
    commit_id: str | None = None,
    turn_ooc_directives: str = "",
) -> tuple[PromptParts, list[str], dict]:
    """Orchestrate turn preparation while leaving each stage testable in isolation."""
    prompts, scene_types, manager_effects = await run_manager_pipeline(
        user_input,
        pc_id,
        npc_id,
        recent_story,
        world_id,
        scenario_id,
        perspective,
        suppress_time_plan,
        scene_need_hints=scene_need_hints,
        pending_kakao_messages=pending_kakao_messages,
        enable_kakao_preprocessing=enable_kakao_preprocessing,
        social_media_features=social_media_features,
        thread_id=thread_id,
        commit_id=commit_id,
        turn_ooc_directives=turn_ooc_directives,
    )

    return prompts, scene_types, manager_effects

# ════════════════════════════════════════════════════════════
# 테스트
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def _test():
        prompts, scene_types, _manager_effects = await run_manager(
            user_input   = "*지희와 아린이 놀러 왔다. 은서와 셋이 소파에 앉아 수다를 떤다.*",
            pc_id        = "sian",
            npc_id       = "eun_seo",
            recent_story = "토요일 오후, 은서네 집.",
            world_id     = "babe_univ",
            perspective  = 3,
        )
        print("=== FIXED ===");   print(prompts.fixed[:200],  "...\n")
        print("=== GENRE ===");   print(prompts.genre[:200] if prompts.genre else "(없음)", "\n")
        print("=== DYNAMIC ==="); print(prompts.dynamic)
        print("\n=== 씬 타입 ==="); print(scene_types)

    asyncio.run(_test())
