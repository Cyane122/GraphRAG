# ================================
# tests/smoke_wiki_manual_audit.py
#
# 외부 Markdown 편집의 baseline 감지·manual archive·inverse·commit 충돌을 검증합니다.
#
# Functions
#   - _character_document() -> str : 두 H2를 가진 canonical character fixture를 반환합니다.
#   - _section_patch(store: WikiStore, section_path: tuple[str, ...], replacement: str) -> SectionPatch : 최신 revision patch를 만듭니다.
#   - _pending(patch: SectionPatch) -> PendingWikiCommit : 테스트용 update commit을 만듭니다.
#   - main() -> None : manual audit smoke assertions를 실행합니다.
# ================================

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from src.apps.app.app import create_app
from src.wiki import (
    PendingWikiCommit,
    SectionPatch,
    WikiCommitQueue,
    WikiRevisionConflict,
    WikiStore,
    document_revision,
    parse_markdown_sections,
)


def _character_document() -> str:
    """수동 편집과 section rebase를 나눠 검증할 canonical character를 반환합니다."""
    return """---
id: character:audit_thread:legacy
type: character
schema_version: 1
visibility: [actor, updater, player]
created_at: 2026-07-24T00:00:00+00:00
world_id: demo_world
thread_id: audit_thread
profile_id: character_profile:legacy
---
# Legacy Character

## 기본 신상

### 이름

- 이름: Legacy

## 현재 상태

### 신체 상태와 감정 상태

- 신체 상태: 안정
- 감정 상태: 평온
"""


def _section_patch(
    store: WikiStore,
    section_path: tuple[str, ...],
    replacement: str,
) -> SectionPatch:
    """현재 character revision을 기준으로 complete section patch를 반환합니다."""
    document = store.read_document("characters/legacy.md")
    section = parse_markdown_sections(document.content)[section_path]
    return SectionPatch(
        document=document.path,
        base_revision=document.revision,
        base_section_revision=document_revision(section.markdown),
        base_markdown=section.markdown,
        section_path=section_path,
        replacement_markdown=replacement,
        evidence="manual audit smoke",
        evidence_source="player_input",
        confidence=1.0,
    )


def _pending(patch: SectionPatch) -> PendingWikiCommit:
    """Section patch 하나를 가진 deterministic 테스트 update commit을 반환합니다."""
    return PendingWikiCommit(
        user_input_hash=sha256(b"audit-user").hexdigest(),
        actor_response_hash=sha256(b"audit-actor").hexdigest(),
        updater_model="manual-audit-smoke",
        patches=[patch],
    )


def main() -> None:
    """외부 section/구조/생성/삭제와 pending 충돌의 감사 보장을 검증합니다."""
    route_paths = {route.path for route in create_app().routes}
    assert "/api/conversations/{thread_id}/wiki/manual-audit" in route_paths
    assert "/api/conversations/{thread_id}/wiki/manual-audit/record" in route_paths

    with TemporaryDirectory() as temporary:
        thread_root = Path(temporary) / "threads" / "audit_thread"
        character_path = thread_root / "characters" / "legacy.md"
        character_path.parent.mkdir(parents=True)
        original = _character_document()
        character_path.write_text(original, encoding="utf-8")
        store = WikiStore(thread_root)
        queue = WikiCommitQueue(store)

        initialized = queue.audit_external_changes()
        assert initialized.status == "initialized"
        assert (thread_root / ".wikirag-audit-baseline.json").is_file()

        section_edit = original.replace("- 감정 상태: 평온", "- 감정 상태: 긴장")
        character_path.write_text(section_edit, encoding="utf-8")
        recorded = queue.audit_external_changes()
        assert recorded.status == "recorded"
        section_archive = queue.load_archive(recorded.manual_commit_id or "")
        assert section_archive.operation == "manual"
        assert len(section_archive.applied_changes) == 1
        assert not section_archive.applied_replacements
        inverse = queue.apply_inverse(section_archive.commit_id)
        assert inverse.status == "applied"
        assert character_path.read_text(encoding="utf-8").strip() == original.strip()
        assert queue.audit_external_changes().status == "clean"

        pre_structural = character_path.read_text(encoding="utf-8")
        structural_edit = pre_structural.replace(
            "# Legacy Character",
            "# Renamed Character",
        )
        character_path.write_text(structural_edit, encoding="utf-8")
        structural = queue.audit_external_changes()
        structural_archive = queue.load_archive(structural.manual_commit_id or "")
        assert len(structural_archive.applied_replacements) == 1
        structural_inverse = queue.apply_inverse(structural_archive.commit_id)
        assert structural_inverse.status == "applied"
        assert character_path.read_text(encoding="utf-8") == pre_structural

        event_path = thread_root / "events" / "manual.md"
        event_path.parent.mkdir(parents=True)
        event_content = "# Manually Created Event\n"
        event_path.write_text(event_content, encoding="utf-8")
        creation = queue.audit_external_changes()
        creation_archive = queue.load_archive(creation.manual_commit_id or "")
        assert len(creation_archive.applied_creations) == 1
        assert queue.apply_inverse(creation_archive.commit_id).status == "applied"
        assert not event_path.exists()

        character_path.unlink()
        deletion = queue.audit_external_changes()
        deletion_archive = queue.load_archive(deletion.manual_commit_id or "")
        assert len(deletion_archive.applied_deletions) == 1
        assert queue.apply_inverse(deletion_archive.commit_id).status == "applied"
        assert character_path.read_text(encoding="utf-8") == pre_structural

        internal_patch = _section_patch(
            store,
            ("현재 상태", "신체 상태와 감정 상태"),
            (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 피곤\n"
                "- 감정 상태: 평온"
            ),
        )
        internal = _pending(internal_patch)
        queue.queue(internal)
        assert queue.apply_pending().commit_id == internal.commit_id
        assert queue.audit_external_changes().status == "clean"

        different_section_patch = _section_patch(
            store,
            ("현재 상태", "신체 상태와 감정 상태"),
            (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 회복\n"
                "- 감정 상태: 평온"
            ),
        )
        different_pending = _pending(different_section_patch)
        queue.queue(different_pending)
        current = character_path.read_text(encoding="utf-8")
        character_path.write_text(
            current.replace("- 이름: Legacy", "- 이름: Edited Externally"),
            encoding="utf-8",
        )
        assert queue.apply_pending().commit_id == different_pending.commit_id
        assert "- 이름: Edited Externally" in character_path.read_text(encoding="utf-8")
        assert queue.audit_external_changes().status == "clean"

        same_section_patch = _section_patch(
            store,
            ("현재 상태", "신체 상태와 감정 상태"),
            (
                "### 신체 상태와 감정 상태\n\n"
                "- 신체 상태: 안정\n"
                "- 감정 상태: 기쁨"
            ),
        )
        same_pending = _pending(same_section_patch)
        queue.queue(same_pending)
        current = character_path.read_text(encoding="utf-8")
        character_path.write_text(
            current.replace("- 감정 상태: 평온", "- 감정 상태: 외부 수정"),
            encoding="utf-8",
        )
        try:
            queue.apply_pending()
        except WikiRevisionConflict:
            pass
        else:
            raise AssertionError("same-section external edit must block pending apply")
        failed = queue.load()
        assert failed is not None and failed.status == "failed"
        assert "- 감정 상태: 외부 수정" in character_path.read_text(encoding="utf-8")
        assert queue.audit_external_changes().status == "clean"

    print("Wiki manual audit smoke checks passed.")


if __name__ == "__main__":
    main()
