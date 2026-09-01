# ================================
# tests/smoke_wiki_runtime_branching.py
#
# Wiki runtime branching smoke checks cover post-apply edits, safe branching, archive export, updater failure recovery, and delete behavior.
#
# Functions
#   - run_runtime_branching_suite(vault_root: Path, handles: RuntimeConversationHandles) -> None : Run the branching, lifecycle, and recovery smoke suite.
#   - main() -> None : Run the standalone runtime branching smoke suite.
# ================================

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.models import ConversationState  # noqa: E402
import src.apps.app.service as app_service  # noqa: E402
import src.apps.app.conversation_lifecycle as conversation_lifecycle  # noqa: E402
import src.apps.app.wiki_branching as wiki_branching  # noqa: E402
import src.apps.app.wiki_controls as wiki_controls  # noqa: E402
import src.apps.app.wiki_message_ops as wiki_message_ops  # noqa: E402
import src.wiki as wiki_package  # noqa: E402
from src.wiki import parse_frontmatter  # noqa: E402
from src.wiki.markdown import parse_markdown_sections  # noqa: E402
from tests.smoke_wiki_runtime_flow import run_runtime_flow_suite  # noqa: E402
from tests.wiki_runtime_smoke_fixtures import (  # noqa: E402
    RuntimeConversationHandles,
    _fake_pending_commit,
    _failing_pending_commit,
    configure_runtime_environment,
    copy_runtime_world,
)

async def run_runtime_branching_suite(
    vault_root: Path,
    handles: RuntimeConversationHandles,
) -> None:
    """Run the branching, lifecycle, and recovery smoke suite."""
    state = handles.state
    store = handles.store
    thread_root = handles.thread_root
    scene_path = handles.scene_path
    before = handles.baseline_scene
    latest_user_id = handles.latest_user_id
    latest_assistant = next(
        message for message in state.messages if message.id == handles.latest_assistant_id
    )
    latest_user = next(
        message for message in state.messages if message.id == latest_user_id
    )
    latest_applied_assistant = state.messages[-1]
    assert latest_applied_assistant.wiki_commit_id is not None
    edited_applied = await wiki_message_ops.edit_wiki_message(
        state,
        latest_applied_assistant.id,
        f"{latest_applied_assistant.content}\n\n적용 후 수정.",
        store,
    )
    assert edited_applied["wiki_update_status"] == "queued"
    assert state.messages[-1].wiki_commit_id == state.wiki_pending_commit_id
    assert any(
        '"operation": "inverse"' in path.read_text(encoding="utf-8")
        for path in (thread_root / "commits").glob("*.md")
    )
    assert wiki_controls.apply_wiki_commit_now(state, store).update_status == "applied"

    # 중간 과거 턴은 원본을 건드리지 않고 턴 직전 상태로 새 thread를 만든다.
    source_scene_before_branch = scene_path.read_text(encoding="utf-8")
    branch_result = wiki_branching.branch_wiki_conversation_before_message(
        state,
        latest_user.id,
        store,
    )
    branch = branch_result.conversation
    branch_root = vault_root / "threads" / branch.thread_id
    assert branch.thread_id != state.thread_id
    assert branch_result.draft == latest_user.content
    assert len(branch.messages) == 1
    assert branch.messages[0].role == "assistant"
    assert not (branch_root / "commit.md").exists()
    assert scene_path.read_text(encoding="utf-8") == source_scene_before_branch
    branch_scene = (branch_root / "scene" / "current.md").read_text(
        encoding="utf-8"
    )
    branch_trigger = parse_markdown_sections(branch_scene)[
        ("시작 기준", "Immediate Trigger")
    ].markdown
    baseline_trigger = parse_markdown_sections(before)[
        ("시작 기준", "Immediate Trigger")
    ].markdown
    assert branch_trigger.rstrip() == baseline_trigger.rstrip(), (
        f"branch trigger mismatch:\n{branch_trigger}\n--- baseline ---\n"
        f"{baseline_trigger}"
    )
    branch_manifest_metadata = parse_frontmatter(
        (branch_root / "thread.md").read_text(encoding="utf-8")
    )
    assert branch_manifest_metadata is not None
    assert branch.thread_id in branch_manifest_metadata.id
    assert state.thread_id not in branch_manifest_metadata.id
    assert f'"thread_id": "{branch.thread_id}"' in (
        branch_root / ".wikirag-runtime.json"
    ).read_text(encoding="utf-8")

    # 연결 archive가 없으면 불완전한 branch를 남기지 않고 중단한다.
    broken_state = state.model_copy(deep=True)
    broken_assistant = next(
        message
        for message in broken_state.messages
        if message.parent_user_id == latest_user.id
    )
    broken_assistant.wiki_commit_id = "missing_commit_archive"
    thread_directories_before = {
        path.name for path in (vault_root / "threads").iterdir()
    }
    try:
        wiki_branching.branch_wiki_conversation_before_message(
            broken_state,
            latest_user.id,
            store,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Missing applied archives must abort safe branching")
    assert {
        path.name for path in (vault_root / "threads").iterdir()
    } == thread_directories_before

    # Wiki 대화 lifecycle은 이름·보관·ZIP 내보내기와 thread 삭제를 동기화한다.
    renamed_branch = conversation_lifecycle.rename_wiki_conversation(
        branch,
        "아침 장면 분기",
        store,
    )
    assert renamed_branch.title == "아침 장면 분기"
    assert conversation_lifecycle.set_wiki_conversation_archived(
        renamed_branch,
        True,
        store,
    ).archived is True
    export_bytes, export_filename = (
        conversation_lifecycle.export_wiki_conversation(renamed_branch)
    )
    assert export_filename.endswith(".zip")
    with ZipFile(BytesIO(export_bytes)) as exported:
        exported_names = set(exported.namelist())
        assert "conversation.json" in exported_names
        assert "wiki_thread/thread.md" in exported_names
        assert not any("debug/" in name for name in exported_names)
        assert not any(name.endswith(".wiki_commit.lock") for name in exported_names)
    conversation_lifecycle.delete_wiki_conversation(
        renamed_branch,
        store,
    )
    assert not store.exists(renamed_branch.thread_id)
    assert not branch_root.exists()

    wiki_package.plan_pending_commit = _failing_pending_commit
    failed_events = [
        event
        async for event in app_service.append_user_and_stream(
            state,
            "Updater 실패 상태를 확인한다.",
            store,
        )
    ]
    assert failed_events[-1]["wiki_update_status"] == "failed"
    assert not (thread_root / "commit.md").exists()
    saved = store.load(state.thread_id)
    assert saved.world_mode == "wiki"
    assert saved.wiki_update_status == "failed"
    assert "mock updater exhausted" in saved.wiki_update_error

    wiki_package.plan_pending_commit = _fake_pending_commit
    recovered = await wiki_controls.retry_wiki_update(saved, store)
    assert recovered.update_status == "queued"
    assert recovered.commit is not None
    wiki_controls.skip_wiki_commit(saved, store, "스모크 테스트 정리")
    deleted_assistant_id = saved.messages[-1].id
    deleted = wiki_message_ops.delete_wiki_message(
        saved,
        deleted_assistant_id,
        store,
    )
    assert all(
        message["id"] != deleted_assistant_id
        for message in deleted["messages"]
    )
    assert not (thread_root / "commit.md").exists()

    no_commit_thread_id = "delete_without_applied_commit"
    (vault_root / "threads" / no_commit_thread_id / "commits").mkdir(parents=True)
    no_commit_state = ConversationState(
        thread_id=no_commit_thread_id,
        world_mode="wiki",
        world_id="babe_university",
        wiki_update_status="applied",
        messages=[
            {"id": "user_without_commit", "role": "user", "content": "삭제할 입력"},
            {
                "id": "assistant_without_commit",
                "role": "assistant",
                "content": "삭제할 응답",
                "parent_user_id": "user_without_commit",
            },
        ],
    )
    no_commit_deleted = wiki_message_ops.delete_wiki_message(
        no_commit_state,
        "assistant_without_commit",
        store,
    )
    assert [message["id"] for message in no_commit_deleted["messages"]] == [
        "user_without_commit"
    ]

def main() -> None:
    """Run the standalone runtime branching smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        vault_root = copy_runtime_world(temporary_root)
        configure_runtime_environment(temporary_root, vault_root)
        handles = asyncio.run(run_runtime_flow_suite(temporary_root, vault_root))
        asyncio.run(run_runtime_branching_suite(vault_root, handles))

    print("smoke_wiki_runtime_branching: ok")

if __name__ == "__main__":
    main()
