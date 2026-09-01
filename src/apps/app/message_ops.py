# ================================
# src/apps/app/message_ops.py
#
# Message mutation operations: reroll, edit, variant activation, and delete.
# These functions mutate conversation state and always call store.save() to persist.
#
# Functions
#   - reroll_assistant(state: ConversationState, assistant_id: str, store: ConversationStore, actor_model: str | None = None) -> dict : Regenerate an assistant message from its paired user input.
#   - edit_message(state: ConversationState, message_id: str, content: str, store: ConversationStore, actor_model: str | None = None) -> dict : Edit a message and update state.
#   - activate_variant(state: ConversationState, message_id: str, version_index: int, store: ConversationStore) -> dict : Activate a specific version of an assistant message by index (oldest-first).
#   - delete_message(state: ConversationState, message_id: str, store: ConversationStore) -> dict : Delete a message and update state.
# ================================

from __future__ import annotations

from copy import deepcopy

from src.apps.app.models import (
    ConversationState,
    MessageVariant,
    _message_payload,
    normalize_actor_model,
)
from src.apps.app.pending_store import discard_pending_commit, save_pending_commit
from src.apps.app.runtime import ActiveConversation, initialize_conversation, restore_game_time
from src.apps.app.storage import ConversationStore


def _preview(content: str) -> str:
    """Import preview_text lazily to avoid a circular import at module load time."""
    from src.apps.app.service import preview_text
    return preview_text(content)


async def _generate(state, content, user_msg_id, store, **kwargs) -> dict:
    """Delegate to service._collect_generation (lazy import avoids load-time circularity)."""
    from src.apps.app.service import _collect_generation, _prepare_generation_input

    effective_input, ooc_result = await _prepare_generation_input(
        state,
        content,
        include_pending_ooc=False,
    )
    kwargs["ooc_result"] = ooc_result
    return await _collect_generation(state, effective_input, user_msg_id, store, **kwargs)


_MAX_HISTORY_TURNS = 10
_RECENT_STORY_TURNS = 3


async def reroll_assistant(
    state: ConversationState,
    assistant_id: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """Regenerate an assistant message from its paired user message."""
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)
    async with ActiveConversation(state):
        assistant_index = next((i for i, msg in enumerate(state.messages) if msg.id == assistant_id), None)
        if assistant_index is None:
            latest_user = next((msg for msg in reversed(state.messages) if msg.role == "user"), None)
            if latest_user and (not state.messages or state.messages[-1].id == latest_user.id):
                selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
                return await _generate(
                    state,
                    latest_user.content,
                    latest_user.id,
                    store,
                    actor_model=selected_actor_model,
                    turn_ooc_directives=latest_user.ooc_config,
                )
            raise KeyError("assistant message not found")
        assistant = state.messages[assistant_index]
        parent = next((msg for msg in state.messages if msg.id == assistant.parent_user_id), None)
        if parent is None:
            raise KeyError("paired user message not found")
        selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
        original_messages = [msg.model_copy(deep=True) for msg in state.messages]
        original_history = deepcopy(state.history)
        original_recent = list(state.recent_responses)
        original_preview = state.preview
        original_pending = deepcopy(state.pending_commit)
        # 보류 커밋은 항상 '최신(미커밋) 응답'의 것이다.
        #  - 최신(미커밋) 응답 reroll: pending을 폐기(그래프 부작용 지연분 무효화) 후
        #    그 시점 스냅샷에서 재생성한다. 그래프 상태와 텍스트가 일관된다.
        #  - 과거(이미 커밋된) 응답 reroll: 폐기할 pending이 없어 그래프를 되돌릴 수 없다.
        #    이 경우 '텍스트만' 재생성한다 — 부모 입력 직전까지의 컨텍스트로 새 응답을 만든 뒤
        #    재생성이 새로 적재한 pending을 폐기하고 기존 최신 pending/히스토리/프리뷰를 복원한다.
        #    그래프는 현재 커밋 상태 그대로 유지되며, 재생성 텍스트와 그래프 간 개연성 불일치는
        #    사용자가 감수한다(명시적 동의).
        is_current = bool(
            state.pending_commit
            and state.pending_commit.get("response_msg_id") == assistant.id
        )
        original_created_at = assistant.created_at
        original_prev_cot = state.prev_cot
        if is_current:
            pending = state.pending_commit
            await restore_game_time(pending.get("prev_game_time"))
            state.history = list(pending.get("history_snapshot") or [])
            state.recent_responses = list(pending.get("recent_snapshot") or [])
            state.prev_cot = str(pending.get("prev_cot") or "")
            discard_pending_commit(pending, state.world_id, state.pc_id, state.npc_id)
            state.pending_commit = None
        else:
            # 과거 응답: 부모 유저 입력 '직전'까지로 컨텍스트를 잘라 재생성한다
            # (이후 턴 내용을 Actor가 컨텍스트로 보지 않도록).
            parent_index = next(
                (i for i, msg in enumerate(state.messages) if msg.id == parent.id), 0
            )
            prior = state.messages[:parent_index]
            state.history = [
                {"role": msg.role, "content": msg.content, "msg_id": msg.id}
                for msg in prior
                if msg.role in {"user", "assistant"}
            ][-_MAX_HISTORY_TURNS * 2:]
            state.recent_responses = [
                msg.content[:1500] for msg in prior if msg.role == "assistant"
            ][-_RECENT_STORY_TURNS:]
        assistant.variants.insert(
            0,
            MessageVariant(
                content=assistant.content,
                created_at=assistant.created_at,
                actor_model=assistant.actor_model,
                edited=assistant.edited,
            ),
        )
        try:
            result = await _generate(
                state,
                parent.content,
                parent.id,
                store,
                actor_model=selected_actor_model,
                turn_ooc_directives=parent.ooc_config,
                persist=False,
            )
            new_payload = result["message"]
            new_message = next((msg for msg in state.messages if msg.id == new_payload["id"]), None)
            if new_message is None:
                raise RuntimeError("Reroll completed without a persisted assistant message.")
            assistant.content = new_message.content
            # 과거 응답은 원래 타임스탬프를 유지한다(메시지 위치는 그대로이므로).
            assistant.created_at = new_message.created_at if is_current else original_created_at
            assistant.parent_user_id = new_message.parent_user_id
            assistant.edited = new_message.edited
            assistant.actor_model = new_message.actor_model
            state.messages = [msg for msg in state.messages if msg.id != new_message.id]
            state.history = [
                {"role": msg.role, "content": msg.content, "msg_id": msg.id}
                for msg in state.messages
                if msg.role in {"user", "assistant"}
            ][-_MAX_HISTORY_TURNS * 2:]
            if is_current:
                if state.pending_commit and state.pending_commit.get("response_msg_id") == new_message.id:
                    state.pending_commit["response_msg_id"] = assistant.id
                    state.pending_commit["ai_response"] = assistant.content
                    save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            else:
                # 과거 응답: 재생성이 새로 만든 pending을 폐기하고 기존 최신 상태를 복원한다.
                if state.pending_commit and state.pending_commit.get("response_msg_id") == new_message.id:
                    discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
                state.pending_commit = original_pending
                if original_pending:
                    save_pending_commit(original_pending, state.world_id, state.pc_id, state.npc_id)
                state.recent_responses = original_recent
                state.preview = original_preview
                state.prev_cot = original_prev_cot
            store.save(state)
            return {
                "message": _message_payload(assistant),
                "pending_commit_id": result.get("pending_commit_id") if is_current else None,
                "preview": state.preview,
            }
        except Exception:
            # 재생성으로 새로 적재된 pending이 있으면(기존과 commit_id가 다르면) 폐기한 뒤 원복한다.
            current_pending = state.pending_commit
            if current_pending and (
                not original_pending
                or current_pending.get("commit_id") != original_pending.get("commit_id")
            ):
                discard_pending_commit(current_pending, state.world_id, state.pc_id, state.npc_id)
            state.messages = original_messages
            state.history = original_history
            state.recent_responses = original_recent
            state.preview = original_preview
            state.prev_cot = original_prev_cot
            state.pending_commit = original_pending
            if state.pending_commit:
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            store.save(state)
            raise


async def edit_message(
    state: ConversationState,
    message_id: str,
    content: str,
    store: ConversationStore,
    actor_model: str | None = None,
) -> dict:
    """Edit a message and update conversation state."""
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)
    async with ActiveConversation(state):
        index = next((i for i, msg in enumerate(state.messages) if msg.id == message_id), None)
        if index is None:
            raise KeyError("message not found")
        message = state.messages[index]
        if message.role == "assistant":
            message.content = content
            message.edited = True
            if state.pending_commit and state.pending_commit.get("response_msg_id") == message.id:
                state.pending_commit["ai_response"] = content
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.preview = _preview(content)
            store.save(state)
            return {"message": _message_payload(message), "preview": state.preview}

        selected_actor_model = normalize_actor_model(actor_model or state.actor_model)
        original_messages = [msg.model_copy(deep=True) for msg in state.messages]
        original_history = deepcopy(state.history)
        original_recent = list(state.recent_responses)
        original_preview = state.preview
        original_pending = deepcopy(state.pending_commit)
        message.content = content
        message.edited = True
        removed_ids = {msg.id for msg in state.messages[index + 1:]}
        if state.pending_commit and state.pending_commit.get("response_msg_id") in removed_ids:
            discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.pending_commit = None
        state.messages = state.messages[: index + 1]
        state.history = [
            {"role": msg.role, "content": msg.content, "msg_id": msg.id}
            for msg in state.messages
            if msg.role in {"user", "assistant"}
        ]
        state.recent_responses = [
            msg.content[:1500] for msg in state.messages if msg.role == "assistant"
        ][-_RECENT_STORY_TURNS:]
        store.save(state)
        try:
            return await _generate(
                state,
                content,
                message.id,
                store,
                actor_model=selected_actor_model,
                turn_ooc_directives=message.ooc_config,
            )
        except Exception:
            if state.pending_commit and original_pending:
                discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            state.messages = original_messages
            state.history = original_history
            state.recent_responses = original_recent
            state.preview = original_preview
            state.pending_commit = original_pending
            if state.pending_commit:
                save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
            store.save(state)
            raise


def activate_variant(
    state: ConversationState,
    message_id: str,
    version_index: int,
    store: ConversationStore,
) -> dict:
    """Activate a specific version of an assistant message by index (oldest-first)."""
    msg = next((m for m in state.messages if m.id == message_id), None)
    if msg is None:
        raise KeyError("message not found")
    if msg.role != "assistant":
        raise ValueError("can only activate variants of assistant messages")

    variants_oldest_first = list(reversed(msg.variants))
    total = len(variants_oldest_first) + 1

    if version_index < 0 or version_index >= total:
        raise ValueError(f"version_index {version_index} out of range [0, {total - 1}]")

    if version_index == total - 1:
        store.save(state)
        return {"message": _message_payload(msg)}

    selected = variants_oldest_first[version_index]
    old_current = MessageVariant(
        content=msg.content,
        created_at=msg.created_at,
        actor_model=msg.actor_model,
        edited=msg.edited,
    )
    remaining = [v for v in msg.variants if v is not selected]
    msg.variants = [old_current] + remaining
    msg.content = selected.content
    msg.actor_model = selected.actor_model
    msg.edited = selected.edited

    state.history = [
        {"role": m.role, "content": m.content, "msg_id": m.id}
        for m in state.messages
        if m.role in {"user", "assistant"}
    ][-_MAX_HISTORY_TURNS * 2:]
    state.recent_responses = [
        m.content[:1500] for m in state.messages if m.role == "assistant"
    ][-_RECENT_STORY_TURNS:]
    state.preview = _preview(msg.content)
    if state.pending_commit and state.pending_commit.get("response_msg_id") == msg.id:
        state.pending_commit["ai_response"] = msg.content
        save_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
    store.save(state)
    return {"message": _message_payload(msg)}


def delete_message(state: ConversationState, message_id: str, store: ConversationStore) -> dict:
    """Delete a message and update conversation state."""
    message = next((msg for msg in state.messages if msg.id == message_id), None)
    if message is None:
        raise KeyError("message not found")
    removed_ids = {message.id}
    if message.role == "user":
        removed_ids.update(msg.id for msg in state.messages if msg.parent_user_id == message.id)
    if state.pending_commit and state.pending_commit.get("response_msg_id") in removed_ids:
        discard_pending_commit(state.pending_commit, state.world_id, state.pc_id, state.npc_id)
        state.pending_commit = None
    state.messages = [msg for msg in state.messages if msg.id not in removed_ids]
    state.history = [
        {"role": msg.role, "content": msg.content, "msg_id": msg.id}
        for msg in state.messages
        if msg.role in {"user", "assistant"}
    ][-_MAX_HISTORY_TURNS * 2:]
    state.recent_responses = [
        msg.content[:1500] for msg in state.messages if msg.role == "assistant"
    ][-_RECENT_STORY_TURNS:]
    latest = next((msg for msg in reversed(state.messages) if msg.role == "assistant"), None)
    state.preview = _preview(latest.content) if latest else "새 대화"
    store.save(state)
    return {"messages": [_message_payload(msg) for msg in state.messages], "preview": state.preview}
