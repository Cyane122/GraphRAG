# ================================
# src/apps/app/wiki_branching.py
#
# 중간 과거 Wiki 턴 직전 상태를 원본과 분리된 새 thread로 재구성합니다.
#
# Functions
#   - branch_wiki_conversation_before_message(state: ConversationState, message_id: str, store: ConversationStore) -> WikiBranchResult : 선택 메시지 직전 상태와 입력 초안을 새 Wiki 대화로 분기합니다.
# ================================

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from src.apps.app.models import (
    ChatMessage,
    ConversationState,
    WikiBranchResult,
)
from src.apps.app.storage import ConversationStore
from src.apps.app.wiki_message_ops import rebuild_wiki_derived_state
from src.config import WIKI_VAULT_ROOT
from src.wiki import (
    WikiCommitError,
    WikiCommitQueue,
    WikiStore,
    ensure_audit_baseline,
)


_FRONTMATTER_BLOCK_RE = re.compile(
    r"\A---\r?\n.*?\r?\n---",
    re.DOTALL,
)


def _new_branch_thread_id(
    state: ConversationState,
    store: ConversationStore,
) -> str:
    """충돌 없는 filesystem-safe branch thread ID를 반환합니다."""
    scenario_id = state.scenario_id or "default"
    while True:
        candidate = (
            f"{state.world_id}__{scenario_id}__branch_{uuid4().hex[:12]}"
        )
        if not store.exists(candidate):
            return candidate


def _target_user_message(
    state: ConversationState,
    message_id: str,
) -> tuple[int, ChatMessage]:
    """선택한 user 또는 assistant에 연결된 user 메시지와 index를 반환합니다."""
    messages_by_id = {message.id: message for message in state.messages}
    selected = messages_by_id.get(message_id)
    if selected is None:
        raise KeyError("message not found")
    if selected.role == "user":
        user = selected
    elif selected.parent_user_id:
        user = messages_by_id.get(selected.parent_user_id)
        if user is None or user.role != "user":
            raise ValueError("선택한 응답의 사용자 입력을 찾을 수 없습니다.")
    else:
        raise ValueError("첫 장면 메시지에서는 과거 턴 분기를 만들 수 없습니다.")
    return state.messages.index(user), user


def _rewrite_thread_metadata(
    thread_root: Path,
    source_thread_id: str,
    branch_thread_id: str,
) -> None:
    """복사한 canonical frontmatter와 runtime marker의 thread ID를 새 값으로 바꿉니다."""
    for path in thread_root.rglob("*.md"):
        relative_parts = path.relative_to(thread_root).parts
        if "commits" in relative_parts:
            continue
        content = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_BLOCK_RE.match(content)
        if match is None or source_thread_id not in match.group(0):
            continue
        rewritten = (
            match.group(0).replace(source_thread_id, branch_thread_id)
            + content[match.end():]
        )
        path.write_text(rewritten, encoding="utf-8")
    marker_path = thread_root / ".wikirag-runtime.json"
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["thread_id"] = branch_thread_id
        marker_path.write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _source_commit_for_message(
    queue: WikiCommitQueue,
    user: ChatMessage,
    assistant: ChatMessage,
) -> str | None:
    """Applied update archive ID를 반환하고 비적용 archive는 건너뜁니다."""
    if assistant.wiki_commit_id:
        try:
            commit = queue.load_archive(assistant.wiki_commit_id)
        except WikiCommitError as exc:
            raise ValueError(
                "과거 메시지에 연결된 Wiki commit archive가 없습니다: "
                f"{assistant.wiki_commit_id}"
            ) from exc
        if commit.status != "applied" or commit.operation != "update":
            return None
        return commit.commit_id
    try:
        return queue.find_applied_turn_commit(
            user_input=user.content,
            actor_response=assistant.content,
            user_message_id=user.id,
            assistant_message_id=assistant.id,
        ).commit_id
    except WikiCommitError as exc:
        raise ValueError(
            "과거 메시지의 applied Wiki commit을 안전하게 식별할 수 없습니다."
        ) from exc


def _rewind_branch_state(
    state: ConversationState,
    target_user_index: int,
    branch_root: Path,
) -> None:
    """선택 user 이후 applied message commit을 역순 inverse합니다."""
    messages_by_id = {message.id: message for message in state.messages}
    queue = WikiCommitQueue(WikiStore(branch_root))
    latest_message_id = state.messages[-1].id if state.messages else ""
    for assistant in reversed(state.messages[target_user_index + 1:]):
        if assistant.role != "assistant" or not assistant.parent_user_id:
            continue
        if (
            assistant.id == latest_message_id
            and state.wiki_update_status in {"queued", "failed", "skipped"}
        ):
            continue
        user = messages_by_id.get(assistant.parent_user_id)
        if user is None or user.role != "user":
            continue
        source_commit_id = _source_commit_for_message(queue, user, assistant)
        if source_commit_id is None:
            continue
        inverse = queue.apply_inverse(source_commit_id)
        if inverse.status not in {"applied", "already_reverted"}:
            raise ValueError(
                "과거 상태 분기 중 수동 편집 충돌이 발생했습니다: "
                f"{inverse.message}"
            )


def branch_wiki_conversation_before_message(
    state: ConversationState,
    message_id: str,
    store: ConversationStore,
) -> WikiBranchResult:
    """선택한 user 입력 직전의 Wiki 상태와 메시지를 새 thread로 분기합니다."""
    if state.world_mode != "wiki":
        raise ValueError("Wiki 대화만 과거 상태로 분기할 수 있습니다.")
    target_user_index, target_user = _target_user_message(state, message_id)
    branch_thread_id = _new_branch_thread_id(state, store)
    vault_root = Path(WIKI_VAULT_ROOT).resolve()
    threads_root = vault_root / "threads"
    source_root = (threads_root / state.thread_id).resolve()
    branch_root = (threads_root / branch_thread_id).resolve()
    if (
        source_root.parent != threads_root
        or branch_root.parent != threads_root
        or not source_root.is_dir()
        or branch_root.exists()
    ):
        raise ValueError("Wiki branch source or destination is invalid.")

    try:
        shutil.copytree(
            source_root,
            branch_root,
            ignore=shutil.ignore_patterns(
                "commit.md",
                ".wiki_commit.lock",
                ".wikirag-audit-baseline.json",
                "debug",
            ),
        )
        _rewrite_thread_metadata(
            branch_root,
            state.thread_id,
            branch_thread_id,
        )
        _rewind_branch_state(state, target_user_index, branch_root)
        # Branches exclude the source baseline, so seed a fresh one from the
        # copied thread's final post-rewrite, post-rewind canonical state.
        ensure_audit_baseline(WikiStore(branch_root))

        branch = state.model_copy(deep=True)
        branch.thread_id = branch_thread_id
        branch.title = f"{state.title} · 분기"
        branch.created_at = datetime.now()
        branch.updated_at = branch.created_at
        branch.messages = [
            message.model_copy(deep=True)
            for message in state.messages[:target_user_index]
        ]
        branch.pending_commit = None
        branch.wiki_update_status = "applied"
        branch.wiki_update_error = ""
        branch.wiki_pending_commit_id = None
        rebuild_wiki_derived_state(branch)
        branch.usernotes = store.load_world_usernotes(branch)
        store.save(branch)
    except Exception:
        if branch_root.is_dir() and branch_root.parent == threads_root:
            shutil.rmtree(branch_root)
        raise

    return WikiBranchResult(
        conversation=branch,
        draft=target_user.content,
        source_thread_id=state.thread_id,
        source_user_message_id=target_user.id,
    )
