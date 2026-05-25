# ================================
# src/agents/manager/pipeline.py
#
# Manager turn-preparation pipeline orchestration.
#
# Functions
#   - run_manager_pipeline(user_input: str, pc_id: str, npc_id: str, recent_story: str, world_id: str | None, perspective: int, suppress_time_plan: bool, deps: ManagerDependencies, scene_need_hints: dict[str, str] | None = None, pending_kakao_messages: list[dict] | None = None, enable_kakao_preprocessing: bool = True, social_media_features: dict | None = None) -> tuple[PromptParts, list[str], dict] : Run turn-preparation pipeline
# ================================

from src.agents.manager.core_context import assemble_core_context
from src.agents.manager.models import ManagerDependencies, PromptParts
from src.agents.manager.planning import bootstrap_manager, classify_scene_and_time
from src.agents.manager.prompting import build_prompt_parts, resolve_prompt_world_config
from src.agents.manager.world_context import fetch_dynamic_world_context
from src.simulation.systems.kakao import process_kakao_before_actor
from src.simulation.systems.personal_facts import extract_personal_facts


async def run_manager_pipeline(
    user_input: str,
    pc_id: str,
    npc_id: str,
    recent_story: str,
    world_id: str | None,
    perspective: int,
    suppress_time_plan: bool,
    deps: ManagerDependencies,
    scene_need_hints: dict[str, str] | None = None,
    pending_kakao_messages: list[dict] | None = None,
    enable_kakao_preprocessing: bool = True,
    social_media_features: dict | None = None,
) -> tuple[PromptParts, list[str], dict]:
    """Run turn preparation and optional pre-Actor KakaoTalk preprocessing."""
    bootstrap = await bootstrap_manager(world_id, perspective, deps)
    scene_plan = await classify_scene_and_time(
        user_input,
        recent_story,
        bootstrap,
        pc_id,
        npc_id,
        suppress_time_plan,
        deps,
    )
    personal_facts = await extract_personal_facts(
        user_input,
        subject_id=pc_id,
        audience_id=npc_id,
        current_dt=scene_plan.current_dt,
        subject_aliases=bootstrap.world.get_npc_name_map(),
    )
    if personal_facts:
        scene_plan.manager_effects["personal_facts"] = personal_facts
        scene_plan.manager_effects.setdefault("pending_effects", []).extend(
            {
                "type": "personal_fact_upsert",
                "target": f"PersonalFact:{fact.get('normalized_key')}",
                "field": "fact_text",
                "old_value": None,
                "new_value": fact.get("fact_text"),
            }
            for fact in personal_facts
        )
    core_context = await assemble_core_context(
        user_input,
        recent_story,
        pc_id,
        npc_id,
        bootstrap,
        scene_plan,
        world_id,
        deps,
        current_turn_personal_facts=personal_facts,
    )
    scene_plan.manager_effects["scene_state"] = core_context.scene_state
    scene_plan.manager_effects["context_plan"] = core_context.context_plan
    world_context = await fetch_dynamic_world_context(
        npc_id,
        pc_id,
        core_context.location_id,
        scene_plan.current_dt,
        user_input,
        core_context.context_plan,
        recent_story,
        preloaded_schedule_context=(
            scene_plan.schedule_context
            if scene_plan.current_dt == scene_plan.base_time
            else None
        ),
        social_media_features=social_media_features,
    )
    kakao_enabled = bool((social_media_features or {}).get("kakao_enabled", False))
    if enable_kakao_preprocessing and kakao_enabled:
        try:
            kakao_turn_context = await process_kakao_before_actor(
                pc_id=pc_id,
                npc_id=npc_id,
                current_time=scene_plan.current_dt,
                pending_player_messages=pending_kakao_messages or [],
                recent_story=recent_story,
                world_hints=world_context,
            )
            if kakao_turn_context:
                world_context["kakao_turn_context"] = kakao_turn_context
                scene_plan.manager_effects["kakao_panel_refresh"] = True
            if pending_kakao_messages:
                scene_plan.manager_effects["kakao_processed"] = True
        except Exception as e:
            print(f"[Kakao] before-actor preprocessing failed (ignored): {e}")

    prompt_world_config = await resolve_prompt_world_config(
        bootstrap.world,
        bootstrap.world_config,
        npc_id,
        pc_id,
        perspective,
    )
    prompts = build_prompt_parts(
        user_input,
        recent_story,
        perspective,
        prompt_world_config,
        scene_plan,
        core_context,
        world_context,
        scene_need_hints=scene_need_hints or {},
    )
    return prompts, scene_plan.scene_types, scene_plan.manager_effects
