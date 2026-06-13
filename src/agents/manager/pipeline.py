# ================================
# src/agents/manager/pipeline.py
#
# Manager turn-preparation pipeline orchestration.
#
# Functions
#   - run_manager_pipeline(...) -> tuple[PromptParts, list[str], dict] : Run turn-preparation pipeline
# ================================

from dataclasses import replace

from src.agents.context.scene_keys import normalize_scene_types
from src.agents.manager.integrated_planner import maybe_run_integrated_planner, validated_context_plan
from src.agents.manager.core_context import assemble_core_context
from src.agents.manager.models import ManagerDependencies, PromptParts
from src.agents.manager.planning import bootstrap_manager, classify_scene_and_time
from src.agents.manager.prompting import build_prompt_parts, resolve_prompt_world_config
from src.config import MANAGER_PLANNER_MODE
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
    thread_id: str | None = None,
    commit_id: str | None = None,
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
    legacy_plan = {
        "scene_types": scene_plan.scene_types,
        "time_parse": scene_plan.time_plan,
        "context_plan": core_context.context_plan,
        "present_character_hints": scene_plan.manager_effects.get("scene_npc_ids") or [],
        "personal_fact_candidates": personal_facts,
        "kakao_reply_intent": {},
    }
    integrated_plan = await maybe_run_integrated_planner(
        user_input=user_input,
        recent_story=recent_story,
        thread_id=thread_id,
        commit_id=commit_id,
        legacy_plan=legacy_plan,
        mode=MANAGER_PLANNER_MODE,
    )
    if MANAGER_PLANNER_MODE == "integrated" and integrated_plan:
        if integrated_plan.scene_types:
            scene_plan.scene_types = normalize_scene_types(integrated_plan.scene_types)
        integrated_context_plan = validated_context_plan(integrated_plan)
        if integrated_context_plan:
            core_context = replace(core_context, context_plan=integrated_context_plan)
            scene_plan.manager_effects["integrated_context_plan_applied"] = True
    scene_plan.manager_effects["scene_state"] = core_context.scene_state
    scene_plan.manager_effects["context_plan"] = core_context.context_plan

    active_npc_ids = [npc.get("char_id") for npc in core_context.active_npcs if npc.get("char_id")]
    ambient_npc_ids = [npc.get("char_id") for npc in core_context.ambient_npcs if npc.get("char_id")]
    scene_plan.manager_effects["scene_npc_ids"] = active_npc_ids
    if ambient_npc_ids:
        scene_plan.manager_effects["ambient_npc_ids"] = ambient_npc_ids

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
                world_context["kakao_turn_context"] = {"messages": kakao_turn_context.get("messages") or []}
                scene_plan.manager_effects["kakao_effects"] = kakao_turn_context.get("effects") or []
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
