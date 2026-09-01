# ================================
# src/apps/app/wiki_message_ops.py
#
# Wiki 대화의 최신 메시지 리롤·수정·버전 선택·삭제와 적용 상태 복구를 처리합니다.
#
# Functions
#   - rebuild_wiki_derived_state(state: ConversationState) -> None : 현재 메시지에서 Actor history, recent story와 preview를 다시 만듭니다.
#   - reroll_wiki_assistant(state: ConversationState, assistant_id: str, store: ConversationStore, actor_model: str | None = None) -> dict : 최신 Wiki 응답을 다시 생성합니다.
#   - edit_wiki_message(state: ConversationState, message_id: str, content: str, store: ConversationStore, actor_model: str | None = None) -> dict : 최신 Wiki 메시지를 수정하고 변경안을 다시 생성합니다.
#   - activate_wiki_variant(state: ConversationState, message_id: str, version_index: int, store: ConversationStore) -> dict : 최신 Wiki 응답의 저장 버전을 활성화합니다.
#   - delete_wiki_message(state: ConversationState, message_id: str, store: ConversationStore) -> dict : 최신 Wiki 메시지와 연결된 변경 상태를 삭제합니다.
# ================================

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.apps.app.models import (
    ChatMessage,
    ConversationState,
    MessageVariant,
    _message_payload,
    normalize_actor_model,
    resolve_wiki_systems,
)
from src.apps.app.storage import ConversationStore
from src.apps.app.wiki_controls import skip_wiki_commit
from src.apps.app.settings import load_settings, wiki_updater_model_name
from src.apps.app.wiki_service import (
    _preview_text,
    _strip_hidden_blocks,
    stream_wiki_turn,
)
from src.config import WIKI_VAULT_ROOT, wiki_system_defaults
from src.simulation.state.models import WikiTurnUpdateRequest
from src.simulation.state.updater import update_accepted_turn
from src.wiki import WikiCommitError, WikiCommitQueue, WikiStore
from src.wiki.paths import wiki_thread_root_for_vault


_MAX_HISTORY_TURNS = 10
_RECENT_STORY_TURNS = 3
_MUTABLE_WIKI_STATUSES = {"queued", "failed", "skipped"}


def _restore_state(state: ConversationState, snapshot: ConversationState) -> None:
    """실패한 재생성 뒤 대화 모델의 모든 필드를 원래 값으로 되돌립니다."""
    for field_name in ConversationState.model_fields:
        setattr(state, field_name, deepcopy(getattr(snapshot, field_name)))


def rebuild_wiki_derived_state(state: ConversationState) -> None:
    """현재 메시지 목록에서 Actor history, recent story와 preview를 다시 만듭니다."""
    state.history = [
        {"role": message.role, "content": message.content, "msg_id": message.id}
        for message in state.messages
        if message.role in {"user", "assistant"}
    ][-_MAX_HISTORY_TURNS * 2:]
    state.recent_responses = [
        _strip_hidden_blocks(message.content)[:1500]
        for message in state.messages
        if message.role == "assistant"
    ][-_RECENT_STORY_TURNS:]
    latest = next(
        (
            message
            for message in reversed(state.messages)
            if message.role == "assistant"
        ),
        None,
    )
    state.preview = _preview_text(latest.content) if latest is not None else "새 대화"


def _latest_pair(
    state: ConversationState,
    *,
    assistant_id: str | None = None,
    user_id: str | None = None,
) -> tuple[ChatMessage, ChatMessage]:
    """최신 사용자·응답 쌍을 반환하고 후속 턴이 있는 과거 변경을 거부합니다."""
    if state.wiki_update_status not in _MUTABLE_WIKI_STATUSES | {"applied"}:
        raise ValueError("이미 Wiki 정본에 반영된 응답은 수정할 수 없습니다.")
    if len(state.messages) < 2:
        raise ValueError("변경할 최신 Wiki 응답이 없습니다.")
    assistant = state.messages[-1]
    if assistant.role != "assistant" or not assistant.parent_user_id:
        raise ValueError("최신 미반영 응답만 변경할 수 있습니다.")
    user = state.messages[-2]
    if user.role != "user" or user.id != assistant.parent_user_id:
        raise ValueError("최신 사용자 입력과 응답의 연결이 올바르지 않습니다.")
    if assistant_id is not None and assistant.id != assistant_id:
        raise ValueError("이미 반영된 과거 응답은 변경할 수 없습니다.")
    if user_id is not None and user.id != user_id:
        raise ValueError("이미 반영된 과거 입력은 변경할 수 없습니다.")
    return user, assistant


def _wiki_commit_queue(state: ConversationState) -> WikiCommitQueue:
    """현재 Wiki thread의 commit queue를 반환합니다."""
    return WikiCommitQueue(
        WikiStore(
            wiki_thread_root_for_vault(Path(WIKI_VAULT_ROOT), state.thread_id)
        )
    )


def _inverse_latest_applied_pair(
    state: ConversationState,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
) -> str | None:
    """최신 applied pair를 되돌리고 생성한 inverse commit ID를 반환합니다."""
    if state.wiki_update_status != "applied":
        return None
    queue = _wiki_commit_queue(state)
    if assistant_message.wiki_commit_id:
        source = queue.load_archive(assistant_message.wiki_commit_id)
    else:
        try:
            source = queue.find_applied_turn_commit(
                user_input=user_message.content,
                actor_response=assistant_message.content,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
            )
        except WikiCommitError as exc:
            if str(exc) != "Applied Wiki commit for the message pair was not found":
                raise
            return None
        assistant_message.wiki_commit_id = source.commit_id
    result = queue.apply_inverse(source.commit_id)
    if result.status == "already_reverted":
        return None
    if result.status != "applied" or result.inverse_commit_id is None:
        raise ValueError(result.message)
    return result.inverse_commit_id


def _restore_failed_regeneration(
    state: ConversationState,
    inverse_commit_id: str | None,
) -> None:
    """실패한 Actor 재생성 전에 수행한 inverse를 다시 적용해 정본을 복구합니다."""
    if inverse_commit_id is None:
        return
    result = _wiki_commit_queue(state).apply_inverse(inverse_commit_id)
    if result.status not in {"applied", "already_reverted"}:
        raise RuntimeError(
            "Wiki regeneration failed and the original applied state could not be restored: "
            f"{result.message}"
        )


async def _replace_wiki_update(
    state: ConversationState,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    store: ConversationStore,
    reason: str,
) -> None:
    """기존 commit.md를 보관하고 현재 메시지 쌍으로 새 변경안을 생성합니다."""
    skip_wiki_commit(state, store, reason)
    try:
        settings = load_settings()
        update_result = await update_accepted_turn(
            WikiTurnUpdateRequest(
                vault_root=Path(WIKI_VAULT_ROOT),
                thread_id=state.thread_id,
                user_input=user_message.content,
                actor_response=assistant_message.content,
                model_name=wiki_updater_model_name(),
                max_attempts=3,
                player_profile_id=state.pc_id,
                actor_profile_id=state.npc_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                thinking_level=settings.wiki_updater_thinking_level,
                wiki_systems=resolve_wiki_systems(
                    state.wiki_system_overrides,
                    wiki_system_defaults(),
                ),
            )
        )
        pending = update_result.pending_wiki_commit
        if pending is None:
            raise RuntimeError("Wiki Updater returned no pending commit")
    except Exception as exc:
        state.wiki_update_status = "failed"
        state.wiki_update_error = str(exc)
        state.wiki_pending_commit_id = None
        store.save(state)
        return
    state.wiki_update_status = "queued"
    state.wiki_update_error = ""
    state.wiki_pending_commit_id = pending.commit_id
    assistant_message.wiki_commit_id = pending.commit_id
    store.save(state)


async def _regenerate_latest_pair(
    state: ConversationState,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    store: ConversationStore,
    *,
    actor_model: str | None,
    edited_user_content: str | None = None,
    retain_variant: bool,
) -> dict:
    """기존 commit을 건드리지 않고 Actor 재생성을 끝낸 뒤 변경안을 교체합니다."""
    snapshot = state.model_copy(deep=True)
    selected_model = normalize_actor_model(actor_model or state.actor_model)
    previous_content = assistant_message.content
    previous_created_at = assistant_message.created_at
    previous_model = assistant_message.actor_model
    previous_edited = assistant_message.edited
    previous_variants = [variant.model_copy(deep=True) for variant in assistant_message.variants]
    if edited_user_content is not None:
        user_message.content = edited_user_content
        user_message.edited = True

    state.messages = state.messages[:-2]
    rebuild_wiki_derived_state(state)
    final_event: dict | None = None
    try:
        async for event in stream_wiki_turn(
            state,
            user_message.content,
            client_message_id=user_message.id,
            actor_model=selected_model,
            apply_pending=False,
            queue_update=False,
        ):
            if event["type"] == "complete":
                final_event = event
        if final_event is None or len(state.messages) < 2:
            raise RuntimeError("Wiki reroll completed without a final response.")

        generated_assistant = state.messages[-1]
        state.messages[-2] = user_message
        generated_assistant.id = assistant_message.id
        generated_assistant.parent_user_id = user_message.id
        if retain_variant:
            generated_assistant.variants = [
                MessageVariant(
                    content=previous_content,
                    created_at=previous_created_at,
                    actor_model=previous_model,
                    edited=previous_edited,
                ),
                *previous_variants,
            ]
        else:
            generated_assistant.variants = []
        rebuild_wiki_derived_state(state)
        await _replace_wiki_update(
            state,
            user_message,
            generated_assistant,
            store,
            "Superseded by Wiki message regeneration",
        )
        store.save(state)
        return {
            "message": _message_payload(generated_assistant),
            "pending_commit_id": state.wiki_pending_commit_id,
            "preview": state.preview,
            "wiki_update_status": state.wiki_update_status,
            "wiki_update_error": state.wiki_update_error,
        }
    except Exception:
        _restore_state(state, snapshot)
        store.save(state)
        raise


async def reroll_wiki_assistant(
    state: ConversationState,
    assistant_id: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """최신 미반영 Wiki 응답을 다시 생성하고 이전 응답을 variant로 보관합니다."""
    user_message, assistant_message = _latest_pair(
        state,
        assistant_id=assistant_id,
    )
    inverse_commit_id = _inverse_latest_applied_pair(
        state,
        user_message,
        assistant_message,
    )
    try:
        return await _regenerate_latest_pair(
            state,
            user_message,
            assistant_message,
            store,
            actor_model=actor_model,
            retain_variant=True,
        )
    except Exception:
        _restore_failed_regeneration(state, inverse_commit_id)
        raise


async def edit_wiki_message(
    state: ConversationState,
    message_id: str,
    content: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """최신 Wiki 사용자 입력은 재생성하고, 최신 응답은 변경안만 다시 생성합니다."""
    normalized = content.strip()
    if not normalized:
        raise ValueError("메시지 내용은 비워 둘 수 없습니다.")
    message = next(
        (candidate for candidate in state.messages if candidate.id == message_id),
        None,
    )
    if message is None:
        raise KeyError("message not found")
    if message.role == "user":
        user_message, assistant_message = _latest_pair(state, user_id=message_id)
        inverse_commit_id = _inverse_latest_applied_pair(
            state,
            user_message,
            assistant_message,
        )
        try:
            return await _regenerate_latest_pair(
                state,
                user_message,
                assistant_message,
                store,
                actor_model=actor_model,
                edited_user_content=normalized,
                retain_variant=False,
            )
        except Exception:
            _restore_failed_regeneration(state, inverse_commit_id)
            raise

    user_message, assistant_message = _latest_pair(
        state,
        assistant_id=message_id,
    )
    _inverse_latest_applied_pair(state, user_message, assistant_message)
    assistant_message.content = normalized
    assistant_message.edited = True
    rebuild_wiki_derived_state(state)
    await _replace_wiki_update(
        state,
        user_message,
        assistant_message,
        store,
        "Superseded by Wiki assistant edit",
    )
    return {
        "message": _message_payload(assistant_message),
        "pending_commit_id": state.wiki_pending_commit_id,
        "preview": state.preview,
        "wiki_update_status": state.wiki_update_status,
        "wiki_update_error": state.wiki_update_error,
    }


async def activate_wiki_variant(
    state: ConversationState,
    message_id: str,
    version_index: int,
    store: ConversationStore,
) -> dict:
    """최신 Wiki 응답의 지정 버전을 활성화하고 그 본문으로 변경안을 다시 생성합니다."""
    user_message, message = _latest_pair(state, assistant_id=message_id)
    variants_oldest_first = list(reversed(message.variants))
    total = len(variants_oldest_first) + 1
    if version_index < 0 or version_index >= total:
        raise ValueError(f"version_index {version_index} out of range [0, {total - 1}]")
    if version_index == total - 1:
        return {"message": _message_payload(message)}

    _inverse_latest_applied_pair(state, user_message, message)
    selected = variants_oldest_first[version_index]
    old_current = MessageVariant(
        content=message.content,
        created_at=message.created_at,
        actor_model=message.actor_model,
        edited=message.edited,
    )
    message.variants = [
        old_current,
        *(variant for variant in message.variants if variant.id != selected.id),
    ]
    message.content = selected.content
    message.actor_model = selected.actor_model
    message.edited = selected.edited
    rebuild_wiki_derived_state(state)
    await _replace_wiki_update(
        state,
        user_message,
        message,
        store,
        "Superseded by Wiki variant activation",
    )
    return {
        "message": _message_payload(message),
        "pending_commit_id": state.wiki_pending_commit_id,
        "preview": state.preview,
        "wiki_update_status": state.wiki_update_status,
        "wiki_update_error": state.wiki_update_error,
    }


def delete_wiki_message(
    state: ConversationState,
    message_id: str,
    store: ConversationStore,
) -> dict:
    """최신 Wiki 메시지를 삭제하고 연결된 적용·미반영 변경을 정리합니다."""
    message = next(
        (candidate for candidate in state.messages if candidate.id == message_id),
        None,
    )
    if message is None:
        raise KeyError("message not found")
    if message.role == "assistant":
        user, assistant = _latest_pair(state, assistant_id=message_id)
        removed_ids = {assistant.id}
    else:
        user, assistant = _latest_pair(state, user_id=message_id)
        removed_ids = {user.id, assistant.id}

    _inverse_latest_applied_pair(state, user, assistant)
    skip_wiki_commit(state, store, "Skipped by Wiki message deletion")
    state.messages = [
        candidate for candidate in state.messages if candidate.id not in removed_ids
    ]
    rebuild_wiki_derived_state(state)
    store.save(state)
    return {
        "messages": [_message_payload(candidate) for candidate in state.messages],
        "preview": state.preview,
        "wiki_update_status": state.wiki_update_status,
    }
