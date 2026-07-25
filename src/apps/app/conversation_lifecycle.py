# ================================
# src/apps/app/conversation_lifecycle.py
#
# Wiki 대화의 이름, 보관, 내보내기와 안전한 영구 삭제를 관리합니다.
#
# Functions
#   - _require_wiki_state(state: ConversationState) -> None : Wiki 대화 전용 작업을 검증합니다.
#   - _wiki_thread_paths(state: ConversationState) -> tuple[Path, Path] : 검증된 threads root와 thread root를 반환합니다.
#   - rename_wiki_conversation(state: ConversationState, title: str, store: ConversationStore) -> ConversationState : Wiki 대화 표시 이름을 바꿉니다.
#   - set_wiki_conversation_archived(state: ConversationState, archived: bool, store: ConversationStore) -> ConversationState : Wiki 대화 보관 상태를 바꿉니다.
#   - export_wiki_conversation(state: ConversationState) -> tuple[bytes, str] : 대화 JSON과 Wiki thread를 ZIP으로 반환합니다.
#   - delete_wiki_conversation(state: ConversationState, store: ConversationStore) -> None : Wiki thread와 대화 기록을 안전하게 삭제합니다.
# ================================

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import shutil
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from src.apps.app.models import ConversationState
from src.apps.app.storage import ConversationStore
from src.config import WIKI_VAULT_ROOT


def _require_wiki_state(state: ConversationState) -> None:
    """Wiki conversation이 아니면 lifecycle 작업을 거부합니다."""
    if state.world_mode != "wiki":
        raise ValueError("Wiki 대화에서만 사용할 수 있습니다.")


def _wiki_thread_paths(state: ConversationState) -> tuple[Path, Path]:
    """검증된 Wiki threads root와 대상 thread root를 반환합니다."""
    threads_root = (Path(WIKI_VAULT_ROOT).resolve() / "threads").resolve()
    thread_root = (threads_root / state.thread_id).resolve()
    if thread_root.parent != threads_root:
        raise ValueError("Wiki thread path escapes the vault root")
    return threads_root, thread_root


def rename_wiki_conversation(
    state: ConversationState,
    title: str,
    store: ConversationStore,
) -> ConversationState:
    """공백을 정리한 사용자 표시 이름을 저장하고 state를 반환합니다."""
    _require_wiki_state(state)
    normalized = title.strip()
    if not normalized:
        raise ValueError("대화 이름은 비워둘 수 없습니다.")
    if len(normalized) > 120:
        raise ValueError("대화 이름은 120자 이하여야 합니다.")
    state.title = normalized
    return store.save(state)


def set_wiki_conversation_archived(
    state: ConversationState,
    archived: bool,
    store: ConversationStore,
) -> ConversationState:
    """Wiki 대화의 보관 여부를 저장하고 state를 반환합니다."""
    _require_wiki_state(state)
    state.archived = archived
    return store.save(state)


def export_wiki_conversation(
    state: ConversationState,
) -> tuple[bytes, str]:
    """대화 상태와 prompt 비가시 Wiki thread 파일을 ZIP으로 직렬화합니다."""
    _require_wiki_state(state)
    _, thread_root = _wiki_thread_paths(state)
    if not thread_root.is_dir():
        raise FileNotFoundError(state.thread_id)

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "conversation.json",
            state.model_dump_json(indent=2),
        )
        for path in sorted(thread_root.rglob("*")):
            if not path.is_file() or path.name == ".wiki_commit.lock":
                continue
            relative = path.relative_to(thread_root)
            if "debug" in relative.parts:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(thread_root):
                raise ValueError("Wiki export contains a path outside the thread")
            archive.write(path, Path("wiki_thread") / relative)

    safe_name = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_"})
        else "_"
        for character in state.title.strip()
    ).strip("_")
    filename = f"{safe_name or state.thread_id}.zip"
    return buffer.getvalue(), filename


def delete_wiki_conversation(
    state: ConversationState,
    store: ConversationStore,
) -> None:
    """Wiki thread를 staging한 뒤 대화 JSON과 함께 영구 삭제합니다."""
    _require_wiki_state(state)
    threads_root, thread_root = _wiki_thread_paths(state)
    staged_root = (
        threads_root / f".deleting_{state.thread_id}_{uuid4().hex}"
    ).resolve()
    if staged_root.parent != threads_root or staged_root.exists():
        raise ValueError("Wiki deletion staging path is invalid")

    moved = False
    if thread_root.is_dir():
        thread_root.replace(staged_root)
        moved = True
    try:
        store.delete(state.thread_id)
    except Exception:
        if moved and staged_root.is_dir() and not thread_root.exists():
            staged_root.replace(thread_root)
        raise
    if moved and staged_root.is_dir():
        try:
            shutil.rmtree(staged_root)
        except Exception:
            if not thread_root.exists():
                staged_root.replace(thread_root)
            store.save(state)
            raise
