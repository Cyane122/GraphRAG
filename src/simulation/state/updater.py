# ================================
# src/simulation/state/updater.py
#
# Accepted Actor turn을 Graph 또는 Wiki mode에 맞게 반영하는 단일 공개 Updater입니다.
#
# Functions
#   - update_accepted_turn(request: GraphTurnUpdateRequest | WikiTurnUpdateRequest) -> TurnUpdateResult : mode에 맞는 저장소 반영을 실행합니다.
# ================================

from __future__ import annotations

import asyncio

from src.simulation.state.models import (
    GraphTurnUpdateRequest,
    TurnUpdateResult,
    WikiTurnUpdateRequest,
)


async def update_accepted_turn(
    request: GraphTurnUpdateRequest | WikiTurnUpdateRequest,
) -> TurnUpdateResult:
    """Accepted turn을 요청 mode의 영속 상태에 반영하고 공통 결과를 반환합니다."""
    if request.mode == "graph":
        from src.simulation.state.graph_apply import apply_graph_actor_response

        ooc_message = await apply_graph_actor_response(
            request.actor_response,
            request.npc_id,
            request.pc_id,
            scene_types=request.scene_types,
            scene_chars=request.scene_chars,
            world_config=request.world_config,
            manager_effects=request.manager_effects,
            history_snapshot=request.history_snapshot,
            recent_snapshot=request.recent_snapshot,
            thread_id=request.thread_id,
            commit_id=request.commit_id,
            user_input=request.user_input,
        )
        return TurnUpdateResult(mode="graph", ooc_message=ooc_message)

    from src.config import (
        WIKI_UPDATER_RECALL_BUDGET,
        WIKI_UPDATER_RECALL_TOKEN_BUDGET,
    )
    from src.wiki import plan_pending_commit
    from src.wiki.commit import WikiCommitQueue
    from src.wiki.context import read_wiki_thread_documents
    from src.wiki.recall import select_recall_documents
    from src.wiki.store import WikiStore

    documents = await asyncio.to_thread(
        read_wiki_thread_documents,
        request.vault_root,
        request.thread_id,
    )
    # 누적 문서가 예산을 넘을 때만 최근성·구조 관련성으로 축소한다(짧은 thread는 무변경).
    # 필수 문서(scene/character/relationship)는 항상 포함되고, Updater는 recall을 넓게 잡는다.
    scene_text = next(
        (
            document.content
            for document in documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        "",
    )
    documents = select_recall_documents(
        documents,
        active_profile_ids={
            profile_id
            for profile_id in (request.actor_profile_id, request.player_profile_id)
            if profile_id
        },
        scene_text=scene_text,
        budget=WIKI_UPDATER_RECALL_BUDGET,
        token_budget=WIKI_UPDATER_RECALL_TOKEN_BUDGET,
    )
    pending = await plan_pending_commit(
        documents=documents,
        user_input=request.user_input,
        actor_response=request.actor_response,
        model_name=request.model_name,
        max_attempts=request.max_attempts,
        player_profile_id=request.player_profile_id,
        actor_profile_id=request.actor_profile_id,
        user_message_id=request.user_message_id,
        assistant_message_id=request.assistant_message_id,
        debug_root=(
            request.vault_root
            / "threads"
            / request.thread_id
            / "debug"
            / "updater"
        ),
    )
    pending.user_message_id = request.user_message_id
    pending.assistant_message_id = request.assistant_message_id
    # 게이트가 켜진 실험적 postprocessor만 추가 호출로 같은 pending에 병합한다(기본 off).
    from src.wiki.postprocess import apply_wiki_postprocessors

    ooc_message = await apply_wiki_postprocessors(
        documents,
        request.user_input,
        request.actor_response,
        pending,
        request.actor_profile_id,
        request.player_profile_id,
        request.model_name,
    )
    queue = WikiCommitQueue(
        WikiStore(request.vault_root / "threads" / request.thread_id)
    )
    await asyncio.to_thread(queue.queue, pending)
    return TurnUpdateResult(
        mode="wiki",
        ooc_message=ooc_message,
        pending_wiki_commit=pending,
    )
