# ================================
# src/simulation/state/wiki_apply.py
#
# Wiki accepted turn의 recall, postprocessor, pending commit queueing을 적용합니다.
#
# Functions
#   - apply_wiki_actor_response(request: WikiTurnUpdateRequest) -> TurnUpdateResult : Wiki accepted turn의 deferred commit을 계획하고 queue에 저장합니다.
# ================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.simulation.state.models import TurnUpdateResult, WikiTurnUpdateRequest


async def apply_wiki_actor_response(
    request: WikiTurnUpdateRequest,
) -> TurnUpdateResult:
    """Plan, enrich, and queue the deferred Wiki commit for an accepted turn."""
    from src.config import (
        WIKI_UPDATER_RECALL_BUDGET,
        WIKI_UPDATER_RECALL_TOKEN_BUDGET,
    )
    from src.wiki import plan_pending_commit
    from src.wiki.commit import WikiCommitQueue
    from src.wiki.context import (
        materialize_scene_active_relationships,
        read_wiki_thread_documents,
    )
    from src.wiki.paths import wiki_thread_root_for_vault
    from src.wiki.recall import select_recall_documents
    from src.wiki.store import WikiStore

    documents = await asyncio.to_thread(
        read_wiki_thread_documents,
        request.vault_root,
        request.thread_id,
    )
    thread_root = wiki_thread_root_for_vault(request.vault_root, request.thread_id)
    # 장면 활성 NPC 각각의 owner->player 관계 원장을 없을 때만 지연 생성한다(결정적 런타임
    # scaffolding이지 모델 creation이 아니다 - Updater 검증 경로 밖에서 처리한다). 새로 만든
    # 문서는 곧바로 documents에 합쳐 같은 턴의 Updater가 patch 대상으로 볼 수 있게 한다.
    new_relationship_documents = await asyncio.to_thread(
        materialize_scene_active_relationships,
        WikiStore(thread_root),
        documents,
        request.thread_id,
        request.player_profile_id,
        datetime.now(timezone.utc).isoformat(),
    )
    documents.extend(new_relationship_documents)
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
        thinking_level=request.thinking_level,
        debug_root=thread_root / "debug" / "updater",
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
        request.wiki_systems,
    )
    queue = WikiCommitQueue(WikiStore(thread_root))
    await asyncio.to_thread(queue.queue, pending)
    return TurnUpdateResult(
        mode="wiki",
        ooc_message=ooc_message,
        pending_wiki_commit=pending,
    )
