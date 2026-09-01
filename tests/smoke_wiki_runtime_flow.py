# ================================
# tests/smoke_wiki_runtime_flow.py
#
# Wiki runtime flow smoke checks cover conversation creation, queued commit lifecycle, reroll and edit operations, and immediate apply behavior.
#
# Functions
#   - run_runtime_flow_suite(temporary_root: Path, vault_root: Path) -> RuntimeConversationHandles : Run the conversation flow smoke suite and return the handles needed by later stages.
#   - main() -> None : Run the standalone runtime flow smoke suite.
# ================================

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import PendingCommitExists, initialize_wiki_conversation  # noqa: E402
from src.apps.app.storage import ConversationStore  # noqa: E402
import src.apps.app.service as app_service  # noqa: E402
import src.apps.app.wiki_controls as wiki_controls  # noqa: E402
import src.apps.app.wiki_message_ops as wiki_message_ops  # noqa: E402
from tests.wiki_runtime_smoke_fixtures import (  # noqa: E402
    RuntimeConversationHandles,
    configure_runtime_environment,
    copy_runtime_world,
)

async def run_runtime_flow_suite(
    temporary_root: Path,
    vault_root: Path,
) -> RuntimeConversationHandles:
    """Run the conversation flow smoke suite and return the handles needed later."""
    store = ConversationStore(temporary_root / "data" / "threads")
    state = app_service.create_conversation(
        "babe_university",
        "lover",
        store,
        world_mode="wiki",
    )
    assert state.world_mode == "wiki"
    assert len(state.messages) == 1
    assert "충전 좀 해줘" in state.messages[0].content
    assert "충전 좀 해줘" in state.recent_responses[0]
    thread_root = vault_root / "threads" / state.thread_id
    scene_path = thread_root / "scene" / "current.md"
    before = scene_path.read_text(encoding="utf-8")
    manifest_path = thread_root / "thread.md"
    manifest_before = manifest_path.read_text(encoding="utf-8")
    initialize_wiki_conversation(
        vault_root,
        state.world_id,
        state.scenario_id or "default",
        state.thread_id,
    )
    assert manifest_path.read_text(encoding="utf-8") == manifest_before

    first_events = [
        event
        async for event in app_service.append_user_and_stream(
            state,
            "좋은 아침. 잘 잤어?",
            store,
        )
    ]
    assert first_events[-1]["wiki_update_status"] == "queued"
    assert state.wiki_update_status == "queued"
    assert state.wiki_pending_commit_id == first_events[-1]["pending_commit_id"]
    assert (thread_root / "commit.md").is_file()
    assert scene_path.read_text(encoding="utf-8") == before

    latest_assistant = state.messages[-1]
    latest_user = state.messages[-2]
    original_response = latest_assistant.content
    rerolled = await wiki_message_ops.reroll_wiki_assistant(
        state,
        latest_assistant.id,
        store,
        actor_model=state.actor_model,
    )
    assert rerolled["wiki_update_status"] == "queued"
    assert state.messages[-1].id == latest_assistant.id
    assert state.messages[-1].variants[0].content == original_response
    assert (thread_root / "commit.md").is_file()
    activated = await wiki_message_ops.activate_wiki_variant(
        state,
        latest_assistant.id,
        0,
        store,
    )
    assert activated["wiki_update_status"] == "queued"
    assert state.messages[-1].content == original_response
    edited_assistant = await wiki_message_ops.edit_wiki_message(
        state,
        latest_assistant.id,
        f"{original_response}\n\n수정된 응답.",
        store,
    )
    assert edited_assistant["wiki_update_status"] == "queued"
    assert state.messages[-1].edited is True
    edited_user = await wiki_message_ops.edit_wiki_message(
        state,
        latest_user.id,
        "좋은 아침. 물부터 마실래?",
        store,
        actor_model=state.actor_model,
    )
    assert edited_user["wiki_update_status"] == "queued"
    assert state.messages[-2].id == latest_user.id
    assert state.messages[-2].edited is True
    assert state.messages[-2].content == "좋은 아침. 물부터 마실래?"
    assert state.messages[-1].parent_user_id == latest_user.id

    status = wiki_controls.get_wiki_commit_status(state)
    assert status.update_status == "queued"
    assert status.commit is not None
    assert status.wiki_thread_generation == "current"
    try:
        await wiki_controls.retry_wiki_update(state, store)
    except PendingCommitExists:
        pass
    else:
        raise AssertionError("Retry must not overwrite a pending commit")

    skipped = wiki_controls.skip_wiki_commit(
        state,
        store,
        "플레이어가 이번 변경을 폐기함",
    )
    assert skipped.update_status == "skipped"
    assert skipped.commit is not None
    assert skipped.commit["status"] == "skipped"
    assert not (thread_root / "commit.md").exists()
    assert scene_path.read_text(encoding="utf-8") == before

    retried = await wiki_controls.retry_wiki_update(state, store)
    assert retried.update_status == "queued"
    assert retried.commit is not None
    assert retried.commit["user_message_id"] == state.messages[-2].id
    assert retried.commit["assistant_message_id"] == state.messages[-1].id
    assert state.messages[-1].wiki_commit_id == retried.commit["commit_id"]
    assert (thread_root / "commit.md").is_file()

    second_events = [
        event
        async for event in app_service.append_user_and_stream(
            state,
            "식빵부터 구울까?",
            store,
        )
    ]
    assert second_events[-1]["type"] == "complete"
    assert "첫 Actor 응답이 확정됨" in scene_path.read_text(encoding="utf-8")
    assert list((thread_root / "commits").glob("*.md"))
    assert (thread_root / "commit.md").is_file()
    try:
        await wiki_message_ops.reroll_wiki_assistant(
            state,
            latest_assistant.id,
            store,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Committed historical Wiki responses must not reroll")
    applied_now = wiki_controls.apply_wiki_commit_now(state, store)
    assert applied_now.update_status == "applied"
    assert applied_now.commit is None
    assert not (thread_root / "commit.md").exists()
    return RuntimeConversationHandles(
        state=state,
        store=store,
        thread_root=thread_root,
        scene_path=scene_path,
        baseline_scene=before,
        latest_user_id=latest_user.id,
        latest_assistant_id=latest_assistant.id,
    )

def main() -> None:
    """Run the standalone runtime flow smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        vault_root = copy_runtime_world(temporary_root)
        configure_runtime_environment(temporary_root, vault_root)
        asyncio.run(run_runtime_flow_suite(temporary_root, vault_root))

    print("smoke_wiki_runtime_flow: ok")

if __name__ == "__main__":
    main()
