# ================================
# src/apps/app/wiki_controls.py
#
# Wiki updater와 지연 commit.md의 사용자 제어 경로를 제공합니다.
#
# Functions
#   - get_wiki_commit_status(state: ConversationState) -> WikiCommitStatusResponse : 현재 Wiki 변경 상태를 조회합니다.
#   - get_wiki_systems(state: ConversationState) -> WikiSystemsResponse : 대화별 Wiki postprocessor 유효값과 authored cycle 캐릭터를 반환합니다.
#   - update_wiki_systems(state: ConversationState, store: ConversationStore, patch: dict[str, bool | None]) -> WikiSystemsResponse : 대화별 Wiki postprocessor override를 갱신합니다.
#   - recover_unlogged_wiki_acceptance(state: ConversationState) -> PendingWikiCommit | None : pointer가 가리키는 적용됐지만 기록되지 못한 update를 재적용 없이 기록합니다. 상태를 바꾸는 제어는 모두 먼저 호출합니다.
#   - accept_pending_wiki_commit(state: ConversationState) -> PendingWikiCommit | None : commit.md를 적용하고 확정 턴을 기록합니다. 적용 후 기록만 실패한 archive는 재적용 없이 기록을 재시도합니다.
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
from hashlib import sha256

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
from src.apps.app.settings import load_settings, wiki_updater_model_name
from src.apps.app.storage import ConversationStore
from src.config import wiki_system_defaults
from src.core.logging import AcceptedTurn, ConversationLogError, append_accepted_turn
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
    describe_wiki_commit_failure,
    diagnose_wiki_scope,
    get_wiki_thread_runtime_status,
    list_wiki_documents,
    plan_manual_edit_audit,
    plan_thread_contract_migration,
)
from src.wiki.character_postprocess import authored_cycle_character_titles
from src.wiki.context import read_wiki_thread_documents
from src.wiki.paths import WIKI_ROOTS, wiki_thread_root


def _commit_queue(thread_id: str) -> WikiCommitQueue:
    """Return the commit queue for one Wiki thread."""
    return WikiCommitQueue(
        WikiStore(wiki_thread_root(WIKI_ROOTS, thread_id))
    )


def _wiki_system_response(state: ConversationState) -> WikiSystemsResponse:
    """현재 대화의 Wiki system 응답 모델을 조립합니다."""
    defaults = wiki_system_defaults()
    documents = read_wiki_thread_documents(WIKI_ROOTS, state.thread_id)
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
        WIKI_ROOTS,
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


def _accepted_wiki_pair(
    state: ConversationState,
    commit: PendingWikiCommit,
) -> tuple[ChatMessage, ChatMessage]:
    """Return the user/assistant pair an applied update commit accepted, verified by hash.

    Commits carrying message IDs resolve only that exact pair; older ID-less commits fall
    back to the newest linked pair whose contents match both commit hashes.
    """
    messages_by_id = {message.id: message for message in state.messages}
    if commit.user_message_id and commit.assistant_message_id:
        candidates = [(
            messages_by_id.get(commit.user_message_id),
            messages_by_id.get(commit.assistant_message_id),
        )]
    else:
        candidates = [
            (messages_by_id.get(message.parent_user_id or ""), message)
            for message in reversed(state.messages)
            if message.role == "assistant"
        ]
    for user, assistant in candidates:
        if (
            user is not None
            and assistant is not None
            and sha256(user.content.encode("utf-8")).hexdigest() == commit.user_input_hash
            and sha256(assistant.content.encode("utf-8")).hexdigest()
            == commit.actor_response_hash
        ):
            return user, assistant
    raise ConversationLogError(
        f"Accepted messages for Wiki commit {commit.commit_id} are missing "
        "or do not match its content hashes"
    )


def _unlogged_applied_commit(
    queue: WikiCommitQueue,
    state: ConversationState,
) -> PendingWikiCommit | None:
    """Return the exact applied update archive the conversation pointer still awaits logging for.

    The pointer is cleared only after accepted-turn logging succeeds, so a pointer whose
    archive is already applied means canonical apply finished but logging did not. Any
    other archive state keeps the previous no-op behavior.
    """
    commit_id = state.wiki_pending_commit_id
    if not commit_id:
        return None
    if not (queue.store.root / "commits" / f"{commit_id}.md").is_file():
        return None
    archived = queue.load_archive(commit_id)
    if archived.status != "applied" or archived.operation != "update":
        return None
    return archived


def _log_applied_commit(state: ConversationState, applied: PendingWikiCommit) -> None:
    """Record an applied update commit's verified pair, then mark the state applied.

    Non-update commits (inverse, manual) are never logged as accepted turns. When logging
    fails, the pointer keeps the exact applied commit ID, no other state changes, and a
    WikiCommitError reports that canonical Markdown was applied but logging must be retried.
    """
    if applied.operation == "update":
        try:
            user, assistant = _accepted_wiki_pair(state, applied)
            append_accepted_turn(
                AcceptedTurn(
                    mode="wiki",
                    world_id=state.world_id,
                    scenario_id=state.scenario_id,
                    thread_id=state.thread_id,
                    commit_id=applied.commit_id,
                    user_message_id=user.id,
                    assistant_message_id=assistant.id,
                    user_input=user.content,
                    ai_response=assistant.content,
                )
            )
        except Exception as exc:
            state.wiki_pending_commit_id = applied.commit_id
            raise WikiCommitError(
                f"Wiki commit {applied.commit_id} was applied to canonical Markdown, "
                f"but accepted-turn logging failed; retry logging: {exc}"
            ) from exc
    state.wiki_update_status = "applied"
    state.wiki_update_error = ""
    state.wiki_pending_commit_id = None


def recover_unlogged_wiki_acceptance(state: ConversationState) -> PendingWikiCommit | None:
    """Log the applied-but-unlogged update the pointer still references, without applying.

    Every control that mutates messages, the pointer, or canonical state calls this first.
    It is a no-op (None) unless the pointer names an applied update archive. On success the
    state becomes `applied` with the pointer cleared, so existing applied-state paths such
    as the latest-pair inverse run normally; on failure it raises before any mutation.
    """
    queue = _commit_queue(state.thread_id)
    applied = _unlogged_applied_commit(queue, state)
    if applied is None:
        return None
    _log_applied_commit(state, applied)
    return applied


def accept_pending_wiki_commit(state: ConversationState) -> PendingWikiCommit | None:
    """Apply the pending commit.md through the queue and record the accepted turn.

    Shared by the automatic pre-turn apply and the explicit apply control. When no
    commit.md is pending, an applied archive still referenced by the conversation pointer
    is logged without applying canonical changes again. If logging fails after canonical
    apply, the pointer keeps that exact commit ID and a WikiCommitError reports that a
    logging retry is needed; the pointer is cleared only after logging succeeds.
    Inverse, manual, and skipped commits are never logged as accepted turns.
    """
    queue = _commit_queue(state.thread_id)
    applied = queue.apply_pending()
    if applied is None:
        applied = _unlogged_applied_commit(queue, state)
        if applied is None:
            return None
    _log_applied_commit(state, applied)
    return applied


def apply_wiki_commit_now(
    state: ConversationState,
    store: ConversationStore,
) -> WikiCommitStatusResponse:
    """Apply current commit.md (or retry its accepted-turn log) and persist control state."""
    try:
        applied = accept_pending_wiki_commit(state)
    except Exception as exc:
        state.wiki_update_status = "failed"
        state.wiki_update_error = describe_wiki_commit_failure(exc)
        store.save(state)
        raise
    if applied is None and state.wiki_update_status == "queued":
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
    """최신 확정 턴으로 commit.md를 생성하고 대화 제어 상태를 저장합니다.

    적용됐지만 기록되지 못한 commit이 남아 있으면 먼저 그 확정 턴을 기록한다. 그 턴은 이미
    정본에 반영됐으므로 같은 턴으로 Updater를 다시 돌리면 이중 적용이 되므로, 복구에 성공하면
    새 변경안을 만들지 않고 applied 상태를 반환한다.
    """
    if await asyncio.to_thread(recover_unlogged_wiki_acceptance, state) is not None:
        store.save(state)
        return get_wiki_commit_status(state)
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
        settings = load_settings()
        update_result = await update_accepted_turn(
            WikiTurnUpdateRequest(
                roots=WIKI_ROOTS,
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
    """Archive current commit.md as skipped, or clear a failed updater state.

    An applied-but-unlogged acceptance is recorded first, so skip never clears its
    recovery pointer; once recovered the state is `applied` and nothing is left to skip.
    """
    if recover_unlogged_wiki_acceptance(state) is not None:
        store.save(state)
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
    return diagnose_wiki_scope(WIKI_ROOTS, state.thread_id, state.world_id)


def get_wiki_document_list(state: ConversationState) -> list[WikiDocumentSummary]:
    """현재 대화의 world 자산과 thread 문서를 Explorer용 요약 목록으로 반환합니다."""
    return list_wiki_documents(WIKI_ROOTS, state.thread_id, state.world_id)


def get_wiki_thread_migration(state: ConversationState) -> WikiThreadMigrationPlan:
    """기존 thread의 런타임 상태 섹션 migration을 변경 없이 미리 봅니다."""
    return plan_thread_contract_migration(WIKI_ROOTS, state.thread_id)


def apply_wiki_thread_migration(
    state: ConversationState,
    store: ConversationStore,
) -> WikiThreadMigrationPlan:
    """기존 thread 상태 계약을 audited manual commit으로 적용하고 상태를 저장합니다.

    pointer를 지우기 전에 적용됐지만 기록되지 못한 확정 턴을 먼저 기록한다.
    """
    if recover_unlogged_wiki_acceptance(state) is not None:
        store.save(state)
    result = apply_thread_contract_migration(WIKI_ROOTS, state.thread_id)
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
    """충돌 없는 applied Wiki commit을 inverse하고 대화 제어 상태를 저장합니다.

    정본을 되돌리기 전에 적용됐지만 기록되지 못한 확정 턴을 먼저 기록한다.
    """
    if recover_unlogged_wiki_acceptance(state) is not None:
        store.save(state)
    result = _commit_queue(state.thread_id).apply_inverse(commit_id)
    if result.status == "applied":
        state.wiki_update_status = "applied"
        state.wiki_update_error = ""
        state.wiki_pending_commit_id = None
        store.save(state)
    return result
