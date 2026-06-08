# ================================
# src/agents/manager/pipeline.py
#
# Manager turn-preparation pipeline orchestration.
#
# Functions
#   - run_manager_pipeline(...) -> tuple[PromptParts, list[str], dict] : Run turn-preparation pipeline (Director 병렬 실행; manager_effects["director_beats"]에 beat 배열 저장)
#   - _apply_director_emotion(char_data: dict, beats: list[dict]) -> None : Director beat에서 NPC 감정을 dynamic_state에 반영 (in-memory)
#   - _read_default_director_scene(scene_type: str) -> str : 공통 Director scene guide 파일 읽기
#   - _build_director_world_rules(world_config: dict, scene_types: list[str]) -> str : Director용 고정 world/scenario/scene-flow 규칙 블록 조립
# ================================

import asyncio
from dataclasses import replace
from pathlib import Path

from src.agents.context.scene_keys import normalize_scene_types
from src.agents.manager.integrated_planner import maybe_run_integrated_planner, validated_context_plan
from src.agents.manager.core_context import assemble_core_context
from src.agents.manager.models import ManagerDependencies, PromptParts
from src.agents.manager.planning import bootstrap_manager, classify_scene_and_time
from src.agents.manager.prompting import build_prompt_parts, resolve_prompt_world_config
from src.config import MANAGER_PLANNER_MODE
from src.agents.director import run_director
from src.agents.manager.world_context import fetch_dynamic_world_context
from src.simulation.systems.kakao import process_kakao_before_actor
from src.simulation.systems.personal_facts import extract_personal_facts

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompt_factory" / "prompts"


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

    # Director에는 현재 컷의 active NPC만 전달한다.
    # active는 같은 장소 후보 중 입력/최근 context에 언급된 인물로만 구성된다.
    director_npcs = core_context.active_npcs
    field_types = bootstrap.world.get_field_types() or None
    _, include_pc_beats = bootstrap.world.resolve_pov()
    director_world_rules = _build_director_world_rules(bootstrap.world_config, scene_plan.scene_types)

    # Director와 world_context 조회를 병렬 실행
    director_task = asyncio.create_task(
        run_director(
            char_data=core_context.char_data,
            npcs=director_npcs,
            relationship=core_context.relationship,
            location=core_context.location_name,
            dt=scene_plan.current_dt,
            recent_story=recent_story,
            user_input=user_input,
            pc_id=pc_id,
            field_types=field_types,
            include_pc_beats=include_pc_beats,
            user_data=core_context.user_data if include_pc_beats else None,
            world_rules=director_world_rules,
        )
    )
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
    director_beats, director_prompt = await director_task
    print(f"[Pipeline] director_beats={len(director_beats) if director_beats else 0}")

    # Director 감정을 dynamic_state에 반영 (in-memory, DB write는 StateUpdater에서)
    if director_beats:
        _apply_director_emotion(core_context.char_data, director_beats)
        scene_plan.manager_effects["director_beats"] = director_beats
    if director_prompt:
        scene_plan.manager_effects["director_prompt"] = director_prompt

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
        director_output=director_beats or None,
    )
    return prompts, scene_plan.scene_types, scene_plan.manager_effects


def _apply_director_emotion(char_data: dict, beats: list[dict]) -> None:
    """Director beat에서 NPC의 마지막 감정을 dynamic_state.mood에 반영한다 (in-memory).

    mood는 행동 기반 필드로 Director→Actor 경로로 전달된다.
    실제 DB 반영은 StateUpdater가 담당한다.
    """
    char_name = char_data.get("name") or char_data.get("id")
    if not char_name:
        return
    npc_beats = [b for b in beats if b.get("char") == char_name]
    if not npc_beats:
        return
    last_emotion = npc_beats[-1].get("emotion")
    if last_emotion:
        char_data.setdefault("dynamic_state", {})["mood"] = last_emotion


def _read_default_director_scene(scene_type: str) -> str:
    """공통 Director scene guide 파일이 있으면 읽고, 없으면 빈 문자열을 반환합니다."""
    path = _PROMPT_DIR / "genre_specific" / "director_scenes" / f"{scene_type}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _build_director_world_rules(world_config: dict, scene_types: list[str]) -> str:
    """Director가 세계/시나리오/현재 씬 흐름 전제를 보도록 prompt asset을 조립한다."""
    prompt_sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts = [
        ("world.md", prompt_sections.get("world")),
        ("scenario.md", prompt_sections.get("scenario")),
        ("alteration", (world_config or {}).get("alteration_section")),
    ]
    scene_descriptions = (world_config or {}).get("scene_descriptions") or {}
    scene_guides = (world_config or {}).get("prompt", {}).get("director_scenes") or {}
    for scene_type in scene_types:
        description = scene_descriptions.get(scene_type)
        if isinstance(description, str) and description.strip():
            parts.append((f"scene:{scene_type}:description", description))
        guide = scene_guides.get(scene_type) or _read_default_director_scene(scene_type)
        if isinstance(guide, str) and guide.strip():
            parts.append((f"scene:{scene_type}:director_flow", guide))
    return "\n\n".join(
        f"### {label}\n{text.strip()}"
        for label, text in parts
        if isinstance(text, str) and text.strip()
    )
