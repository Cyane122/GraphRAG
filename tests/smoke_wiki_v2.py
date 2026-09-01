# ================================
# tests/smoke_wiki_v2.py
#
# Wiki V2 smoke runner orchestrates the split policy, creation, postprocess, vault, and commit lifecycle checks.
#
# Functions
#   - _check_markdown_section_parsing(document: WikiDocument) -> None : Validate the sample character section parsing behavior.
#   - _check_event_commit_lifecycle(store: WikiStore, document: WikiDocument, scene_document: WikiDocument, queue: WikiCommitQueue, event_pending: PendingWikiCommit) -> None : Validate event creation, inverse, restore, and conflict planning.
#   - _check_goal_round_trip(document: WikiDocument, queue: WikiCommitQueue, store: WikiStore, goal_pending: PendingWikiCommit) -> None : Validate goal creation apply/inverse round-trip.
#   - _queue_retry_commit(document: WikiDocument, queue: WikiCommitQueue, store: WikiStore, pending: PendingWikiCommit) -> PendingWikiCommit : Queue the retried pending commit for later apply/inverse checks.
#   - _check_pending_rebase_apply(store: WikiStore, document: WikiDocument, queue: WikiCommitQueue, pending: PendingWikiCommit) -> None : Validate automatic rebase when another section changes first.
#   - _check_inverse_preserves_manual_lines(store: WikiStore, document: WikiDocument, queue: WikiCommitQueue, pending: PendingWikiCommit) -> WikiDocument : Validate inverse behavior when the section changed on other lines.
#   - _check_conflicting_inverse_no_write(store: WikiStore, queue: WikiCommitQueue, reverted: WikiDocument) -> None : Validate conflict-only inverse behavior without writing markdown.
#   - _check_crash_recovery_and_skip(store: WikiStore, document: WikiDocument, queue: WikiCommitQueue) -> None : Validate crash recovery, failed apply handling, and skip archiving.
#   - _check_queue_race(root: Path) -> None : Validate pending-queue single-winner behavior across queue instances.
#   - main() -> None : Run the full Wiki V2 smoke suite and print the compatibility marker.
# ================================

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.wiki import (  # noqa: E402
    PendingCommitExists,
    PendingWikiCommit,
    SectionPatch,
    WikiCommitQueue,
    WikiDocument,
    WikiStore,
)
from src.wiki.markdown import document_revision, parse_markdown_sections  # noqa: E402
from tests.smoke_wiki_creation import run_creation_suite  # noqa: E402
from tests.smoke_wiki_policy import run_policy_suite  # noqa: E402
from tests.smoke_wiki_postprocess import run_postprocess_suite  # noqa: E402
from tests.smoke_wiki_vault import run_vault_suite  # noqa: E402
from tests.wiki_smoke_fixtures import create_base_store  # noqa: E402

def _check_markdown_section_parsing(document: WikiDocument) -> None:
    """Validate the sample character section parsing behavior."""
    assert document.metadata is not None
    assert document.metadata.model_extra["description"].startswith(
        "## frontmatter 섹션이 아님"
    )

    sections = parse_markdown_sections(document.content)
    assert ("기본 신상", "나이와 생년월일") in sections
    assert not any("실제 섹션이 아님" in path for path in sections)
    assert ("메모", "들여쓴 실제 섹션") in sections
    assert ("메모", "C#") in sections

def _check_event_commit_lifecycle(
    store: WikiStore,
    document: WikiDocument,
    scene_document: WikiDocument,
    queue: WikiCommitQueue,
    event_pending: PendingWikiCommit,
) -> None:
    """Validate event creation, inverse, restore, and conflict planning."""
    assert len(event_pending.creations) == 2
    assert event_pending.creations[0].document == "events/library-power-outage.md"
    assert (
        event_pending.creations[1].document
        == "memories/character-a-remembers-outage.md"
    )
    assert "## 발생 정보" in event_pending.creations[0].content
    assert "## 사건 내용" in event_pending.creations[0].content
    assert "## 진행 상태" in event_pending.creations[0].content
    assert "- 상태: concluded" in event_pending.creations[0].content
    assert "- 진행 경과: The occurrence concluded in this turn." in (
        event_pending.creations[0].content
    )
    assert "- 종료 시각: 2026-07-23 13:10" in (
        event_pending.creations[0].content
    )
    assert "visibility: [actor, updater]" in event_pending.creations[1].content
    assert "owner: \"character_profile:character_a\"" in (
        event_pending.creations[1].content
    )
    assert "- 관련 사건: Library Power Outage" in (
        event_pending.creations[1].content
    )
    assert "event:library-power-outage" not in (
        event_pending.creations[1].content
    )
    assert "source_commit_id" in event_pending.creations[0].content
    assert "source_user_message_id" in event_pending.creations[0].content
    queue = WikiCommitQueue(store)
    queue.queue(event_pending)
    event_applied = queue.apply_pending()
    assert event_applied is not None
    assert len(event_applied.applied_creations) == 2
    event_path = store.resolve_path("events/library-power-outage.md")
    memory_path = store.resolve_path(
        "memories/character-a-remembers-outage.md"
    )
    assert event_path.exists()
    assert memory_path.exists()
    event_inverse = queue.apply_inverse(event_pending.commit_id)
    assert event_inverse.status == "applied"
    assert not event_path.exists()
    assert not memory_path.exists()
    assert event_inverse.inverse_commit_id is not None
    restore_inverse = queue.apply_inverse(event_inverse.inverse_commit_id)
    assert restore_inverse.status == "applied"
    assert event_path.exists()
    assert memory_path.exists()
    restored_event = store.read_document("events/library-power-outage.md")
    edited_event = store.write_document(
        restored_event.path,
        restored_event.content.replace(
            "The presentation deadline remains active",
            "A manually revised deadline remains active",
        ),
        expected_revision=restored_event.revision,
    )
    assert restore_inverse.inverse_commit_id is not None
    creation_conflict = queue.plan_inverse(restore_inverse.inverse_commit_id)
    assert creation_conflict.status == "conflict"
    store.write_document(
        edited_event.path,
        restored_event.content,
        expected_revision=edited_event.revision,
    )

def _check_goal_round_trip(
    document: WikiDocument,
    queue: WikiCommitQueue,
    store: WikiStore,
    goal_pending: PendingWikiCommit,
) -> None:
    """Validate goal creation apply/inverse round-trip."""
    asyncio.run(run_postprocess_suite())
    queue.queue(goal_pending)
    goal_applied = queue.apply_pending()
    assert goal_applied is not None and len(goal_applied.applied_creations) == 1
    goal_path = store.resolve_path("goals/pass-exam.md")
    assert goal_path.exists()
    goal_inverse = queue.apply_inverse(goal_pending.commit_id)
    assert goal_inverse.status == "applied"
    assert not goal_path.exists()

def _queue_retry_commit(
    document: WikiDocument,
    queue: WikiCommitQueue,
    store: WikiStore,
    pending: PendingWikiCommit,
) -> PendingWikiCommit:
    """Queue the retried pending commit for later apply and inverse checks."""
    assert pending.updater_attempts == 3
    queue.queue(pending)
    assert "신체 상태: 안정" in store.read_document(document.path).content
    assert store.resolve_path("commit.md").exists()
    return pending

def _check_pending_rebase_apply(
    store: WikiStore,
    document: WikiDocument,
    queue: WikiCommitQueue,
    pending: PendingWikiCommit,
) -> None:
    """Validate automatic rebase when another section changes first."""
    store.resolve_path(document.path).write_text(
        document.content.replace("대학생", "대학원생"),
        encoding="utf-8",
    )

    applied = queue.apply_pending()
    assert applied is not None and applied.status == "applied"
    assert len(applied.applied_changes) == 1
    applied_change = applied.applied_changes[0]
    assert "신체 상태: 안정" in applied_change.before_markdown
    assert "계단을 올라 숨이 차고 피곤하다" in applied_change.after_markdown
    assert applied_change.before_revision != applied_change.after_revision
    assert "계단을 올라 숨이 차고 피곤하다" in store.read_document(document.path).content
    assert "대학원생" in store.read_document(document.path).content
    assert not store.resolve_path("commit.md").exists()
    assert store.resolve_path(f"commits/{pending.commit_id}.md").exists()

def _check_inverse_preserves_manual_lines(
    store: WikiStore,
    document: WikiDocument,
    queue: WikiCommitQueue,
    pending: PendingWikiCommit,
) -> WikiDocument:
    """Validate inverse behavior when the section changed on other lines."""
    manually_edited = store.read_document(document.path)
    store.write_document(
        document.path,
        manually_edited.content.replace(
            "- 감정 상태: 평온",
            "- 감정 상태: 차분\n- 사용자 메모: 유지",
        ),
        expected_revision=manually_edited.revision,
    )
    inverse_plan = queue.plan_inverse(pending.commit_id)
    assert inverse_plan.status == "ready"
    inverse_result = queue.apply_inverse(pending.commit_id)
    assert inverse_result.status == "applied"
    assert inverse_result.inverse_commit_id is not None
    reverted = store.read_document(document.path)
    assert "- 신체 상태: 안정" in reverted.content
    assert "- 감정 상태: 차분" in reverted.content
    assert "- 사용자 메모: 유지" in reverted.content
    inverse_archive = queue.load_archive(inverse_result.inverse_commit_id)
    assert inverse_archive.operation == "inverse"
    assert inverse_archive.source_commit_id == pending.commit_id
    repeated_inverse_plan = queue.plan_inverse(pending.commit_id)
    assert repeated_inverse_plan.status == "already_reverted", (
        repeated_inverse_plan.model_dump_json(indent=2)
    )
    return reverted

def _check_conflicting_inverse_no_write(
    store: WikiStore,
    queue: WikiCommitQueue,
    reverted: WikiDocument,
) -> None:
    """Validate conflict-only inverse behavior without writing markdown."""
    reverted_sections = parse_markdown_sections(reverted.content)
    reverted_state = reverted_sections[("현재 상태", "신체 상태와 감정 상태")]
    conflicting_source = PendingWikiCommit(
        user_input_hash="conflict-user",
        actor_response_hash="conflict-actor",
        updater_model="test",
        patches=[
            SectionPatch(
                document=reverted.path,
                base_revision=reverted.revision,
                base_section_revision=document_revision(reverted_state.markdown),
                base_markdown=reverted_state.markdown,
                section_path=("현재 상태", "신체 상태와 감정 상태"),
                replacement_markdown=reverted_state.markdown.replace(
                    "- 신체 상태: 안정",
                    "- 신체 상태: 매우 피곤",
                ),
                evidence="신체 상태가 매우 피곤해졌다.",
                confidence=1.0,
            )
        ],
    )
    queue.queue(conflicting_source)
    assert queue.apply_pending() is not None
    conflict_current = store.read_document(reverted.path)
    store.write_document(
        conflict_current.path,
        conflict_current.content.replace(
            "- 신체 상태: 매우 피곤",
            "- 신체 상태: 수동으로 회복",
        ),
        expected_revision=conflict_current.revision,
    )
    conflict_before = store.read_document(reverted.path).content
    conflict_plan = queue.plan_inverse(conflicting_source.commit_id)
    assert conflict_plan.status == "conflict"
    assert len(conflict_plan.conflicts) == 1
    assert queue.apply_inverse(conflicting_source.commit_id).status == "conflict"
    assert store.read_document(reverted.path).content == conflict_before
    assert not store.resolve_path("commit.md").exists()

def _check_crash_recovery_and_skip(
    store: WikiStore,
    document: WikiDocument,
    queue: WikiCommitQueue,
) -> None:
    """Validate crash recovery, failed apply handling, and skip archiving."""
    current = store.read_document(document.path)
    current_sections = parse_markdown_sections(current.content)
    job_section = current_sections[("기본 신상", "직업과 소속")]
    crash_recovery = PendingWikiCommit(
        user_input_hash="user",
        actor_response_hash="actor",
        updater_model="test",
        patches=[
            SectionPatch(
                document=current.path,
                base_revision=current.revision,
                base_section_revision=document_revision(job_section.markdown),
                base_markdown=job_section.markdown,
                section_path=("기본 신상", "직업과 소속"),
                replacement_markdown="### 직업과 소속\n\n- 직업: 작가",
                evidence="직업이 작가로 바뀌었다.",
                confidence=1.0,
            )
        ],
    )
    queue.queue(crash_recovery)
    store.apply_patches(crash_recovery.patches)
    recovered_crash = queue.apply_pending()
    assert recovered_crash is not None
    assert recovered_crash.applied_changes[0].before_markdown == job_section.markdown
    assert "작가" in recovered_crash.applied_changes[0].after_markdown
    assert "작가" in store.read_document(current.path).content

    current = store.read_document(document.path)
    current_sections = parse_markdown_sections(current.content)
    job_section = current_sections[("기본 신상", "직업과 소속")]
    conflict = PendingWikiCommit(
        user_input_hash="user-2",
        actor_response_hash="actor-2",
        updater_model="test",
        patches=[
            SectionPatch(
                document=current.path,
                base_revision=current.revision,
                base_section_revision=document_revision(job_section.markdown),
                section_path=("기본 신상", "직업과 소속"),
                replacement_markdown="### 직업과 소속\n\n- 직업: 개발자",
                evidence="직업이 개발자로 바뀌었다.",
                confidence=1.0,
            )
        ],
    )
    queue.queue(conflict)
    store.resolve_path(current.path).write_text(
        current.content.replace("작가", "휴학생"),
        encoding="utf-8",
    )
    try:
        queue.apply_pending()
    except Exception:
        pass
    else:
        raise AssertionError("Manual edit must cause a revision conflict")
    loaded = queue.load()
    assert loaded is not None and loaded.status == "failed"
    assert "휴학생" in store.read_document(current.path).content
    skipped = queue.skip_pending("수동 편집을 유지함")
    assert skipped is not None and skipped.status == "skipped"
    assert skipped.failure_reason
    assert skipped.resolution_reason == "수동 편집을 유지함"
    assert not store.resolve_path("commit.md").exists()
    skipped_archive = store.resolve_path(f"commits/{skipped.commit_id}.md")
    assert skipped_archive.exists()
    assert queue._load_path(skipped_archive).status == "skipped"

def _check_queue_race(root: Path) -> None:
    """Validate pending-queue single-winner behavior across queue instances."""
    race_store = WikiStore(root / "queue-race")
    race_queues = [WikiCommitQueue(race_store), WikiCommitQueue(race_store)]
    race_commits = [
        PendingWikiCommit(
            user_input_hash=f"user-{index}",
            actor_response_hash=f"actor-{index}",
            updater_model="test",
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(queue_item.queue, commit_item)
            for queue_item, commit_item in zip(race_queues, race_commits)
        ]
        successes = 0
        failures = 0
        for future in futures:
            try:
                future.result()
                successes += 1
            except PendingCommitExists:
                failures += 1
    assert (successes, failures) == (1, 1)
    race_winner = race_queues[0].load()
    assert race_winner is not None
    same_id_different_payload = race_winner.model_copy(
        update={"summary": "different payload"}
    )
    try:
        race_queues[0].queue(same_id_different_payload)
    except PendingCommitExists:
        pass
    else:
        raise AssertionError("Same commit_id with different payload must conflict")

def main() -> None:
    """Run the full Wiki V2 smoke suite and print the compatibility marker."""
    with TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        run_vault_suite(root)
        store, document, scene_document = create_base_store(root)
        _check_markdown_section_parsing(document)
        retry_pending = asyncio.run(run_policy_suite(document, scene_document))
        event_pending, goal_pending = asyncio.run(
            run_creation_suite(document, scene_document)
        )
        queue = WikiCommitQueue(store)
        _check_event_commit_lifecycle(
            store, document, scene_document, queue, event_pending
        )
        asyncio.run(run_postprocess_suite())
        _check_goal_round_trip(document, queue, store, goal_pending)
        pending = _queue_retry_commit(document, queue, store, retry_pending)
        _check_pending_rebase_apply(store, document, queue, pending)
        reverted = _check_inverse_preserves_manual_lines(store, document, queue, pending)
        _check_conflicting_inverse_no_write(store, queue, reverted)
        _check_crash_recovery_and_skip(store, document, queue)
        _check_queue_race(root)

    print("smoke_wiki_v2: ok")

if __name__ == "__main__":
    main()
