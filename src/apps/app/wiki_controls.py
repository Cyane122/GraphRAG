# ================================
# src/apps/app/wiki_controls.py
#
# Wiki updater와 지연 commit.md의 사용자 제어 경로를 제공합니다.
#
# Functions
#   - get_wiki_commit_status(state: ConversationState) -> WikiCommitStatusResponse : 현재 Wiki 변경 상태를 조회합니다.
#   - get_wiki_systems(state: ConversationState) -> WikiSystemsResponse : 대화별 Wiki postprocessor 유효값과 authored cycle 캐릭터를 반환합니다.
#   - update_wiki_systems(state: ConversationState, store: ConversationStore, patch: dict[str, bool | None]) -> WikiSystemsResponse : 대화별 Wiki postprocessor override를 갱신합니다.
#   - apply_wiki_commit_now(state: ConversationState, store: ConversationStore) -> WikiCommitStatusResponse : 현재 commit.md를 즉시 적용합니다.
#   - retry_wiki_update(state: ConversationState, store: ConversationStore) -> WikiCommitStatusResponse : 마지막 확정 턴으로 Updater를 재실행합니다.
#   - regenerate_wiki_update(state: ConversationState, store: ConversationStore) -> WikiCommitStatusResponse : 기존 변경안을 보존하고 마지막 확정 턴의 commit.md를 새로 생성합니다.
#   - skip_wiki_commit(state: ConversationState, store: ConversationStore, reason: str = "") -> WikiCommitStatusResponse : 현재 변경안을 적용하지 않고 보관합니다.
#   - get_wiki_diagnostics(state: ConversationState) -> list[WikiDiagnostic] : 현재 대화 범위의 문서 무결성 진단을 반환합니다.
#   - get_wiki_document_list(state: ConversationState) -> list[WikiDocumentSummary] : 현재 대화 범위의 문서를 Explorer용 요약으로 반환합니다.
#   - get_wiki_thread_migration(state: ConversationState) -> WikiThreadMigrationPlan : 기존 thread 상태 계약 migration을 미리 봅니다.
#   - apply_wiki_thread_migration(state: ConversationState, store: ConversationStore) -> WikiThreadMigrationPlan : 상태 계약 migration을 audited manual commit으로 적용합니다.
#   - get_wiki_manual_audit(state: ConversationState) -> WikiManualAuditResult : 외부 Markdown 변경을 쓰기 없이 미리 봅니다.
#   - record_wiki_manual_audit(state: ConversationState) -> WikiManualAuditResult : 외부 Markdown 변경을 manual archive로 기록합니다.
#   - plan_wiki_commit_inverse(state: ConversationState, commit_id: str) -> WikiInversePlan : applied commit을 쓰기 없이 inverse 판정합니다.
#   - apply_wiki_commit_inverse(state: ConversationState, store: ConversationStore, commit_id: str) -> WikiInversePlan : 충돌 없는 applied commit을 audited inverse로 적용합니다.
# ================================

from __future__ import annotations

import asyncio
from pathlib import Path

from src.apps.app.models import (
    ChatMessage,
    ConversationState,
    WikiCommitStatusResponse,
    WikiSystemsResponse,
    WikiUpdateStatus,
    apply_wiki_system_patch,
    overridden_wiki_system_names,
    resolve_wiki_systems,
)
from src.apps.app.storage import ConversationStore
from src.config import MODEL_PRO_UPDATER, WIKI_VAULT_ROOT, wiki_system_defaults
from src.simulation.state.models import WikiTurnUpdateRequest
from src.simulation.state.updater import update_accepted_turn
from src.wiki import (
    PendingCommitExists,
    PendingWikiCommit,
    WikiCommitError,
    WikiCommitQueue,
    WikiDiagnostic,
    WikiDocumentSummary,
    WikiStore,
    WikiInversePlan,
    WikiManualAuditResult,
    WikiThreadMigrationPlan,
    apply_thread_contract_migration,
    diagnose_wiki_scope,
    get_wiki_thread_runtime_status,
    list_wiki_documents,
    plan_manual_edit_audit,
    plan_thread_contract_migration,
)
from src.wiki.character_postprocess import authored_cycle_character_titles
from src.wiki.context import read_wiki_thread_documents


def _commit_queue(thread_id: str) -> WikiCommitQueue:
    """Return the commit queue for one Wiki thread."""
    thread_root = Path(WIKI_VAULT_ROOT) / "threads" / thread_id
    return WikiCommitQueue(WikiStore(thread_root))


def _wiki_system_response(state: ConversationState) -> WikiSystemsResponse:
    """현재 대화의 Wiki system 응답 모델을 조립합니다."""
    defaults = wiki_system_defaults()
    documents = read_wiki_thread_documents(Path(WIKI_VAULT_ROOT), state.thread_id)
    return WikiSystemsResponse(
        systems=resolve_wiki_systems(state.wiki_system_overrides, defaults),
        defaults=defaults,
        overridden=overridden_wiki_system_names(state.wiki_system_overrides),
        authored_cycle_characters=authored_cycle_character_titles(documents),
    )


def _status_from_commit(commit: PendingWikiCommit) -> WikiUpdateStatus:
    """Map a commit artifact status to the conversation-facing updater status."""
    if commit.status == "pending":
        return "queued"
    return commit.status


def _latest_turn_pair(state: ConversationState) -> tuple[ChatMessage, ChatMessage]:
    """Return the newest linked user and assistant pair eligible for updater retry."""
    messages_by_id = {message.id: message for message in state.messages}
    for assistant in reversed(state.messages):
        if assistant.role != "assistant" or not assistant.parent_user_id:
            continue
        user = messages_by_id.get(assistant.parent_user_id)
        if user is not None and user.role == "user":
            return user, assistant
    raise ValueError("재시도할 확정 사용자/Actor 응답 쌍이 없습니다.")


def get_wiki_systems(state: ConversationState) -> WikiSystemsResponse:
    """대화별 Wiki postprocessor 유효값과 authored cycle 캐릭터를 반환합니다."""
    return _wiki_system_response(state)


def update_wiki_systems(
    state: ConversationState,
    store: ConversationStore,
    patch: dict[str, bool | None],
) -> WikiSystemsResponse:
    """대화별 Wiki postprocessor override를 갱신하고 저장합니다."""
    state.wiki_system_overrides = apply_wiki_system_patch(
        state.wiki_system_overrides,
        patch,
    )
    store.save(state)
    return _wiki_system_response(state)


def get_wiki_commit_status(state: ConversationState) -> WikiCommitStatusResponse:
    """Return current updater state with commit.md as the authoritative payload."""
    pending = _commit_queue(state.thread_id).load()
    runtime_status = get_wiki_thread_runtime_status(
        Path(WIKI_VAULT_ROOT),
        state.thread_id,
    )
    runtime_fields = {
        "wiki_thread_generation": runtime_status.generation,
        "wiki_thread_diagnostic": runtime_status.message,
    }
    if pending is None:
        if state.wiki_update_status == "queued":
            return WikiCommitStatusResponse(
                update_status="failed",
                update_error="대화에는 queued 상태가 남아 있지만 commit.md가 없습니다.",
                **runtime_fields,
            )
        return WikiCommitStatusResponse(
            update_status=state.wiki_update_status,
            update_error=state.wiki_update_error,
            **runtime_fields,
        )
    return WikiCommitStatusResponse(
        update_status=_status_from_commit(pending),
        update_error=pending.failure_reason or state.wiki_update_error,
        commit=pending.model_dump(mode="json"),
        **runtime_fields,
    )


def apply_wiki_commit_now(
    state: ConversationState,
    store: ConversationStore,
) -> WikiCommitStatusResponse:
    """Apply current commit.md immediately and persist the resulting control state."""
    try:
        applied = _commit_queue(state.thread_id).apply_pending()
    except Exception as exc:
        state.wiki_update_status = "failed"
        state.wiki_update_error = str(exc)
        store.save(state)
        raise
    if applied is not None:
        state.wiki_update_status = "applied"
        state.wiki_update_error = ""
        state.wiki_pending_commit_id = None
    elif state.wiki_update_status == "queued":
        state.wiki_update_status = "failed"
        state.wiki_update_error = "대화에는 queued 상태가 남아 있지만 commit.md가 없습니다."
        state.wiki_pending_commit_id = None
        store.save(state)
        raise WikiCommitError(state.wiki_update_error)
    store.save(state)
    return get_wiki_commit_status(state)


async def _run_wiki_update(
    state: ConversationState,
    store: ConversationStore,
    *,
    replace_pending: bool,
) -> WikiCommitStatusResponse:
    """최신 확정 턴으로 commit.md를 생성하고 대화 제어 상태를 저장합니다."""
    queue = _commit_queue(state.thread_id)
    current = queue.load()
    if current is not None and current.status == "pending" and not replace_pending:
        raise PendingCommitExists(
            "적용 대기 중인 commit.md가 있습니다. 먼저 반영하거나 건너뛰어 주세요."
        )
    user_message, assistant_message = _latest_turn_pair(state)
    if current is not None:
        reason = (
            "Superseded by explicit Wiki regeneration"
            if replace_pending
            else "Superseded by updater retry"
        )
        await asyncio.to_thread(queue.skip_pending, reason)
        if assistant_message.wiki_commit_id == current.commit_id:
            assistant_message.wiki_commit_id = None
    try:
        update_result = await update_accepted_turn(
            WikiTurnUpdateRequest(
                vault_root=Path(WIKI_VAULT_ROOT),
                thread_id=state.thread_id,
                user_input=user_message.content,
                actor_response=assistant_message.content,
                model_name=MODEL_PRO_UPDATER,
                max_attempts=3,
                player_profile_id=state.pc_id,
                actor_profile_id=state.npc_id,
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
    except Exception as exc:
        state.wiki_update_status = "failed"
        state.wiki_update_error = str(exc)
        state.wiki_pending_commit_id = None
        store.save(state)
        raise
    state.wiki_update_status = "queued"
    state.wiki_update_error = ""
    state.wiki_pending_commit_id = pending.commit_id
    assistant_message.wiki_commit_id = pending.commit_id
    store.save(state)
    return get_wiki_commit_status(state)


async def retry_wiki_update(
    state: ConversationState,
    store: ConversationStore,
) -> WikiCommitStatusResponse:
    """Regenerate commit.md after failure without replacing a normal pending commit."""
    return await _run_wiki_update(state, store, replace_pending=False)


async def regenerate_wiki_update(
    state: ConversationState,
    store: ConversationStore,
) -> WikiCommitStatusResponse:
    """Archive any current change proposal and create a fresh commit.md."""
    return await _run_wiki_update(state, store, replace_pending=True)


def skip_wiki_commit(
    state: ConversationState,
    store: ConversationStore,
    reason: str = "",
) -> WikiCommitStatusResponse:
    """Archive current commit.md as skipped, or clear a failed updater state."""
    skipped = _commit_queue(state.thread_id).skip_pending(reason)
    if skipped is None and state.wiki_update_status not in {"queued", "failed"}:
        return get_wiki_commit_status(state)
    state.wiki_update_status = "skipped"
    state.wiki_update_error = ""
    state.wiki_pending_commit_id = None
    store.save(state)
    response = get_wiki_commit_status(state)
    if skipped is not None:
        response.commit = skipped.model_dump(mode="json")
    return response


def get_wiki_diagnostics(state: ConversationState) -> list[WikiDiagnostic]:
    """현재 대화의 world 자산과 thread 문서 무결성 진단을 반환합니다."""
    return diagnose_wiki_scope(Path(WIKI_VAULT_ROOT), state.thread_id, state.world_id)


def get_wiki_document_list(state: ConversationState) -> list[WikiDocumentSummary]:
    """현재 대화의 world 자산과 thread 문서를 Explorer용 요약 목록으로 반환합니다."""
    return list_wiki_documents(Path(WIKI_VAULT_ROOT), state.thread_id, state.world_id)


def get_wiki_thread_migration(state: ConversationState) -> WikiThreadMigrationPlan:
    """기존 thread의 런타임 상태 섹션 migration을 변경 없이 미리 봅니다."""
    return plan_thread_contract_migration(Path(WIKI_VAULT_ROOT), state.thread_id)


def apply_wiki_thread_migration(
    state: ConversationState,
    store: ConversationStore,
) -> WikiThreadMigrationPlan:
    """기존 thread 상태 계약을 audited manual commit으로 적용하고 상태를 저장합니다."""
    result = apply_thread_contract_migration(Path(WIKI_VAULT_ROOT), state.thread_id)
    if result.status == "applied":
        state.wiki_update_status = "applied"
        state.wiki_update_error = ""
        state.wiki_pending_commit_id = None
        store.save(state)
    return result


def get_wiki_manual_audit(state: ConversationState) -> WikiManualAuditResult:
    """현재 baseline 밖의 외부 Markdown 변경을 쓰기 없이 미리 봅니다."""
    return plan_manual_edit_audit(_commit_queue(state.thread_id).store).result


def record_wiki_manual_audit(state: ConversationState) -> WikiManualAuditResult:
    """외부 Markdown 변경을 별도 applied manual commit archive로 기록합니다."""
    return _commit_queue(state.thread_id).audit_external_changes()


def plan_wiki_commit_inverse(
    state: ConversationState,
    commit_id: str,
) -> WikiInversePlan:
    """Applied Wiki commit을 변경 없이 검사해 inverse 계획을 반환합니다."""
    return _commit_queue(state.thread_id).plan_inverse(commit_id)


def apply_wiki_commit_inverse(
    state: ConversationState,
    store: ConversationStore,
    commit_id: str,
) -> WikiInversePlan:
    """충돌 없는 applied Wiki commit을 inverse하고 대화 제어 상태를 저장합니다."""
    result = _commit_queue(state.thread_id).apply_inverse(commit_id)
    if result.status == "applied":
        state.wiki_update_status = "applied"
        state.wiki_update_error = ""
        state.wiki_pending_commit_id = None
        store.save(state)
    return result
