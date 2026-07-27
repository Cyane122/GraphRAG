# ================================
# src/apps/app/wiki_service.py
#
# Wiki 모드의 Actor 스트리밍과 지연 Markdown 업데이트를 조율합니다.
#
# Functions
#   - stream_wiki_turn(state: ConversationState, content: str, client_message_id: str | None = None, actor_model: str | None = None, apply_pending: bool = True, queue_update: bool = True) -> AsyncIterator[dict] : 한 Wiki 사용자 턴을 스트리밍하고 필요할 때 commit.md를 생성합니다.
# ================================

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from pathlib import Path
import re
from uuid import uuid4

from src.agents.prompt_factory.usernote import build_usernotes_block
from src.agents.manager.classifier import classify_scene_types
from src.apps.app.actor import stream_actor_events
from src.apps.app.models import (
    ChatMessage,
    ConversationState,
    normalize_actor_model,
    resolve_wiki_systems,
)
from src.apps.app.output_guard import find_forbidden_terms, find_pov_violations
from src.apps.app.output_repair import repair_actor_output
from src.apps.app.settings import load_settings
from src.apps.app.turn_debug import write_actor_raw_snapshot, write_turn_debug_snapshot
from src.config import (
    MAX_TOKEN,
    MODEL_OUTPUT_REPAIR,
    MODEL_PRO_UPDATER,
    WIKI_VAULT_ROOT,
    wiki_system_defaults,
)
from src.core.llm.client import get_client
from src.simulation.state.models import WikiTurnUpdateRequest
from src.simulation.state.updater import update_accepted_turn
from src.wiki import (
    WikiConversationSetup,
    WikiPromptBundle,
    apply_pending_wiki_commit,
    build_wiki_prompt_bundle,
    get_wiki_thread_runtime_status,
)
from src.wiki.models import WikiDocument
from src.wiki.secret_guard import find_hidden_secret_leaks


logger = logging.getLogger(__name__)

_GENAI_CLIENT = get_client()
_LOGS_DIR = Path("logs")
_TURN_DEBUG_DIR = _LOGS_DIR / "turn_debug"
_MAX_HISTORY_TURNS = 10
_RECENT_STORY_TURNS = 3


def _setup_from_state(state: ConversationState) -> WikiConversationSetup:
    """영속화된 앱 상태를 Wiki 런타임 설정 모델로 변환합니다."""
    config = state.world_config
    return WikiConversationSetup(
        world_id=state.world_id,
        scenario_id=state.scenario_id or "default",
        thread_id=state.thread_id,
        pc_id=state.pc_id,
        pc_name=str(config.get("pc_name") or state.pc_id),
        npc_id=state.npc_id,
        npc_name=state.npc_name_kor,
        pov_mode=str(config.get("pov_mode") or "3p_char"),
        perspective=state.perspective,
        rating=str(config.get("rating") or "r18"),
        opening_scene="",
    )


def _strip_hidden_blocks(value: str) -> str:
    """Actor 응답에서 사용자에게 숨길 analyze와 OOC 블록을 제거합니다."""
    text = re.sub(r"<analyze>[\s\S]*?</analyze>", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<ooc>[\s\S]*?</ooc>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _preview_text(value: str) -> str:
    """Wiki assistant 응답의 사이드바 미리보기를 반환합니다."""
    text = re.sub(r"\*\*[^*]+\*\*", "", _strip_hidden_blocks(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:25] + "..." if len(text) > 26 else text or "새 대화"


def _message_payload(message: ChatMessage) -> dict:
    """Wiki 턴에서 생성한 메시지를 프런트엔드 JSON으로 변환합니다."""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "createdAt": message.created_at.strftime("%H:%M"),
        "parentUserId": message.parent_user_id,
        "edited": message.edited,
        "actorModel": message.actor_model,
        "oocConfig": message.ooc_config,
        "wikiCommitId": message.wiki_commit_id,
        "variants": [
            {
                "id": variant.id,
                "content": variant.content,
                "createdAt": variant.created_at.strftime("%H:%M"),
                "actorModel": variant.actor_model,
                "edited": variant.edited,
            }
            for variant in message.variants
        ],
    }


def _wiki_debug_effects(
    bundle: WikiPromptBundle,
    setup: WikiConversationSetup,
) -> dict[str, object]:
    """Turn debug에서 Wiki materialization과 문서 선택을 확인할 진단값을 반환합니다."""
    scene_document = next(
        (
            document
            for document in bundle.updater_documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        None,
    )
    runtime_status = get_wiki_thread_runtime_status(
        Path(WIKI_VAULT_ROOT),
        setup.thread_id,
    )
    return {
        "engine": "wiki",
        "wiki_context": {
            "thread_id": setup.thread_id,
            "scenario_id": setup.scenario_id,
            "scene_document": scene_document.path if scene_document else None,
            "scene_revision": scene_document.revision if scene_document else None,
            "start_state_materialized": bool(
                scene_document and "## 시작 기준" in scene_document.content
            ),
            "start_state_in_dynamic_prompt": "## 시작 기준" in bundle.dynamic_prompt,
            "thread_generation": runtime_status.generation,
            "thread_format_version": runtime_status.format_version,
            "thread_generation_diagnostic": runtime_status.message,
        },
        "updater_documents": [
            {
                "path": document.path,
                "revision": document.revision,
                "type": document.metadata.type if document.metadata else None,
                "visibility": list(document.metadata.visibility) if document.metadata else [],
            }
            for document in bundle.updater_documents
        ],
    }


async def _repair_wiki_response(
    full_response: str,
    visible_text: str,
    state: ConversationState,
    documents: list[WikiDocument],
) -> str:
    """기존 출력 guard 설정으로 Wiki Actor의 가시 본문을 필요할 때 수정합니다."""
    target = visible_text or full_response
    secret_leaks = find_hidden_secret_leaks(target, documents)
    settings = load_settings()
    if not settings.output_repair_enabled:
        if secret_leaks:
            raise RuntimeError("Wiki Actor output disclosed a hidden Secret")
        return full_response
    blocked = find_forbidden_terms(target) + find_pov_violations(
        target,
        state.perspective,
        state.npc_name_kor,
    )
    blocked.extend(
        (
            "Hidden Secret disclosure. Remove or conceal this private truth: "
            f"{leak.actual_content}"
        )
        for leak in secret_leaks
    )
    if not blocked:
        return full_response
    repaired = await repair_actor_output(target, blocked, MODEL_OUTPUT_REPAIR)
    remaining = find_forbidden_terms(repaired) + find_pov_violations(
        repaired,
        state.perspective,
        state.npc_name_kor,
    )
    remaining_secret_leaks = find_hidden_secret_leaks(repaired, documents)
    if remaining_secret_leaks:
        raise RuntimeError(
            "Wiki Actor output disclosed a hidden Secret after repair"
        )
    if remaining:
        raise RuntimeError(
            "Wiki Actor output failed the output guard after repair: "
            + ", ".join(remaining[:8])
        )
    if visible_text and visible_text in full_response:
        return full_response.replace(visible_text, repaired, 1)
    return repaired


async def stream_wiki_turn(
    state: ConversationState,
    content: str,
    client_message_id: str | None = None,
    actor_model: str | None = None,
    *,
    apply_pending: bool = True,
    queue_update: bool = True,
) -> AsyncIterator[dict]:
    """이전 Wiki commit 적용부터 Actor 스트리밍과 새 commit 보류까지 실행합니다."""
    if state.world_mode != "wiki":
        raise ValueError("stream_wiki_turn requires world_mode='wiki'")

    if apply_pending:
        try:
            applied = await asyncio.to_thread(
                apply_pending_wiki_commit,
                WIKI_VAULT_ROOT,
                state.thread_id,
            )
        except Exception as exc:
            state.wiki_update_status = "failed"
            state.wiki_update_error = str(exc)
            raise
        if applied is not None:
            state.wiki_update_status = "applied"
            state.wiki_update_error = ""
            state.wiki_pending_commit_id = None
    user_message = ChatMessage(
        id=client_message_id or f"user_{uuid4().hex}",
        role="user",
        content=content,
        ooc_config=state.ooc_config,
    )
    state.messages.append(user_message)
    yield {"type": "user", "message": _message_payload(user_message)}

    selected_model = normalize_actor_model(actor_model or state.actor_model)
    state.actor_model = selected_model
    recent_story = "\n".join(state.recent_responses[-_RECENT_STORY_TURNS:])
    effective_input = content
    note_block = build_usernotes_block(state.usernotes)
    if note_block:
        effective_input = f"{note_block}\n\n{content}"

    yield {"type": "status", "content": "최신 Wiki 문서를 읽고 프롬프트를 조립하는 중입니다."}
    setup = _setup_from_state(state)
    scene_types = await classify_scene_types(content, recent_story)
    bundle = await asyncio.to_thread(
        build_wiki_prompt_bundle,
        WIKI_VAULT_ROOT,
        setup,
        effective_input,
        recent_story,
        state.ooc_config,
        scene_types,
    )
    debug_dir = write_turn_debug_snapshot(
        user_input=effective_input,
        fixed_prompt=bundle.fixed_prompt,
        genre_prompt=bundle.genre_prompt,
        dynamic_prompt=bundle.dynamic_prompt,
        scene_types=bundle.scene_types,
        manager_effects=_wiki_debug_effects(bundle, setup),
        history=state.history,
        world_id=state.world_id,
        pc_id=state.pc_id,
        npc_id=state.npc_id,
        npc_name=state.npc_name_kor,
        logs_dir=_LOGS_DIR,
        turn_debug_dir=_TURN_DEBUG_DIR,
        actor_model=selected_model,
    )

    actor_kwargs = {
        "fixed_prompt": bundle.fixed_prompt,
        "genre_prompt": bundle.genre_prompt,
        "dynamic_prompt": bundle.dynamic_prompt,
        "history": state.history,
        "genai_client": _GENAI_CLIENT,
        "model_name": selected_model,
        "max_token": MAX_TOKEN,
    }
    final_event: dict | None = None
    async for event in stream_actor_events(**actor_kwargs):
        if event["type"] == "complete":
            final_event = event
            break
        yield event
    if final_event is None:
        raise RuntimeError("Wiki Actor stream ended without a complete event")
    if not str(final_event.get("visible_text") or "").strip():
        logger.warning("[WikiGeneration] Actor produced empty visible prose; retrying once")
        async for event in stream_actor_events(**actor_kwargs):
            if event["type"] == "complete":
                if str(event.get("visible_text") or "").strip():
                    final_event = event
                break
            yield event
    if not str(final_event.get("visible_text") or "").strip():
        raise RuntimeError("Wiki Actor produced no visible prose after retry")

    write_actor_raw_snapshot(
        full_response=str(final_event.get("content") or ""),
        raw_thinking=str(final_event.get("raw_thinking") or ""),
        visible_text=str(final_event.get("visible_text") or ""),
        logs_dir=_LOGS_DIR,
        debug_dir=debug_dir,
    )
    full_response = await _repair_wiki_response(
        str(final_event.get("content") or ""),
        str(final_event.get("visible_text") or ""),
        state,
        bundle.updater_documents,
    )
    assistant_message = ChatMessage(
        id=f"assistant_{uuid4().hex}",
        role="assistant",
        content=full_response,
        parent_user_id=user_message.id,
        actor_model=selected_model,
    )
    state.messages.append(assistant_message)
    state.history.extend([
        {"role": "user", "content": content, "msg_id": user_message.id},
        {"role": "assistant", "content": full_response, "msg_id": assistant_message.id},
    ])
    del state.history[:-_MAX_HISTORY_TURNS * 2]
    visible = _strip_hidden_blocks(full_response)
    state.recent_responses.append(visible[:1500])
    state.recent_responses = state.recent_responses[-_RECENT_STORY_TURNS:]
    state.prev_cot = str(final_event.get("raw_thinking") or "")
    state.preview = _preview_text(full_response)
    state.title = f"{state.world_id}/{state.scenario_id}"
    state.pending_commit = None

    update_status = state.wiki_update_status
    update_error = state.wiki_update_error
    pending_commit_id = state.wiki_pending_commit_id
    if queue_update:
        yield {"type": "status", "content": "응답에서 Wiki 변경 사항을 추출하는 중입니다."}
        update_status = "queued"
        update_error = ""
        pending_commit_id = None
        try:
            update_result = await update_accepted_turn(
                WikiTurnUpdateRequest(
                    vault_root=Path(WIKI_VAULT_ROOT),
                    thread_id=state.thread_id,
                    user_input=content,
                    actor_response=full_response,
                    model_name=MODEL_PRO_UPDATER,
                max_attempts=3,
                player_profile_id=setup.pc_id,
                actor_profile_id=setup.npc_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                wiki_systems=resolve_wiki_systems(
                    state.wiki_system_overrides,
                    wiki_system_defaults(),
                ),
            )
        )
            pending = update_result.pending_wiki_commit
            if pending is None:
                raise RuntimeError("Wiki Updater returned no pending commit")
            pending_commit_id = pending.commit_id
            assistant_message.wiki_commit_id = pending.commit_id
        except Exception as exc:
            update_status = "failed"
            update_error = str(exc)
            logger.exception("[WikiGeneration] updater failed after retries")

        state.wiki_update_status = update_status
        state.wiki_update_error = update_error
        state.wiki_pending_commit_id = pending_commit_id

    yield {
        "type": "complete",
        "message": _message_payload(assistant_message),
        "pending_commit_id": pending_commit_id,
        "preview": state.preview,
        "wiki_update_status": update_status,
        "wiki_update_error": update_error,
    }
