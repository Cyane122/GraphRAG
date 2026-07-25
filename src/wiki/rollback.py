# ================================
# src/wiki/rollback.py
#
# Applied Wiki commit의 section·문서 inverse와 보수적 3-way merge를 계획합니다.
#
# Functions
#   - plan_applied_commit_inverse(store: WikiStore, commit: PendingWikiCommit) -> WikiInversePlan : 수동 편집을 보존할 inverse patch 또는 충돌 자료를 반환합니다.
# ================================

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import (
    DocumentCreation,
    DocumentDeletion,
    DocumentReplacement,
    PendingWikiCommit,
    SectionPatch,
    WikiInverseConflict,
    WikiInversePlan,
)
from src.wiki.store import WikiStore


@dataclass(frozen=True)
class _LineHunk:
    """Base line 범위 하나를 replacement로 바꾸는 diff hunk입니다."""

    start: int
    end: int
    replacement: tuple[str, ...]


def _line_hunks(base: str, target: str) -> list[_LineHunk]:
    """Base에서 target으로 가는 line 단위 변경 hunk를 반환합니다."""
    base_lines = base.splitlines(keepends=True)
    target_lines = target.splitlines(keepends=True)
    matcher = SequenceMatcher(None, base_lines, target_lines, autojunk=False)
    return [
        _LineHunk(start=i1, end=i2, replacement=tuple(target_lines[j1:j2]))
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _hunks_overlap(left: _LineHunk, right: _LineHunk) -> bool:
    """두 base 좌표 hunk가 같은 줄 또는 같은 삽입 지점을 수정하는지 반환합니다."""
    if left == right:
        return False
    left_insert = left.start == left.end
    right_insert = right.start == right.end
    if left_insert and right_insert:
        return left.start == right.start
    if left_insert:
        return right.start <= left.start <= right.end
    if right_insert:
        return left.start <= right.start <= left.end
    return max(left.start, right.start) < min(left.end, right.end)


def _merge_inverse(
    before_markdown: str,
    after_markdown: str,
    current_markdown: str,
) -> tuple[str | None, str | None]:
    """After→before inverse와 after→current 수동 편집을 line 단위로 합칩니다."""
    inverse_hunks = _line_hunks(after_markdown, before_markdown)
    manual_hunks = _line_hunks(after_markdown, current_markdown)
    remaining_inverse_hunks: list[_LineHunk] = []
    for inverse_hunk in inverse_hunks:
        inverse_already_present = False
        for manual_hunk in manual_hunks:
            if not _hunks_overlap(inverse_hunk, manual_hunk):
                continue
            if inverse_hunk.replacement and any(
                manual_hunk.replacement[index:index + len(inverse_hunk.replacement)]
                == inverse_hunk.replacement
                for index in range(
                    len(manual_hunk.replacement)
                    - len(inverse_hunk.replacement)
                    + 1
                )
            ):
                inverse_already_present = True
                break
            return None, "Manual edit overlaps the inverse change."
        if not inverse_already_present:
            remaining_inverse_hunks.append(inverse_hunk)

    combined = list(manual_hunks)
    for inverse_hunk in remaining_inverse_hunks:
        if inverse_hunk not in combined:
            combined.append(inverse_hunk)
    merged_lines = after_markdown.splitlines(keepends=True)
    for hunk in sorted(combined, key=lambda item: (item.start, item.end), reverse=True):
        merged_lines[hunk.start:hunk.end] = list(hunk.replacement)
    return "".join(merged_lines), None


def plan_applied_commit_inverse(
    store: WikiStore,
    commit: PendingWikiCommit,
) -> WikiInversePlan:
    """Applied archive를 읽어 안전한 section inverse patch 또는 충돌을 반환합니다."""
    has_audit = bool(
        commit.applied_changes
        or commit.applied_creations
        or commit.applied_deletions
        or commit.applied_replacements
    )
    if commit.status != "applied" or not has_audit:
        return WikiInversePlan(
            source_commit_id=commit.commit_id,
            status="unsupported",
            message="Applied section audit is unavailable for this commit.",
        )

    documents = {
        change.document: store.read_document(change.document)
        for change in commit.applied_changes
    }
    sections_by_document = {
        path: parse_markdown_sections(document.content)
        for path, document in documents.items()
    }
    patches: list[SectionPatch] = []
    creations: list[DocumentCreation] = []
    deletions: list[DocumentDeletion] = []
    replacements: list[DocumentReplacement] = []
    conflicts: list[WikiInverseConflict] = []
    for change in commit.applied_changes:
        document = documents[change.document]
        section = sections_by_document[change.document].get(change.section_path)
        if section is None:
            conflicts.append(
                WikiInverseConflict(
                    document=change.document,
                    section_path=change.section_path,
                    reason="Target section no longer exists.",
                    before_markdown=change.before_markdown,
                    after_markdown=change.after_markdown,
                    current_markdown="",
                )
            )
            continue
        current_revision = document_revision(section.markdown)
        if current_revision == change.before_revision:
            continue
        if current_revision == change.after_revision:
            merged = change.before_markdown
            merge_error = None
        else:
            merged, merge_error = _merge_inverse(
                change.before_markdown,
                change.after_markdown,
                section.markdown,
            )
        if merged is None:
            conflicts.append(
                WikiInverseConflict(
                    document=change.document,
                    section_path=change.section_path,
                    reason=merge_error or "Inverse merge failed.",
                    before_markdown=change.before_markdown,
                    after_markdown=change.after_markdown,
                    current_markdown=section.markdown,
                )
            )
            continue
        if merged.rstrip("\r\n") == section.markdown.rstrip("\r\n"):
            continue
        patches.append(
            SectionPatch(
                document=change.document,
                base_revision=document.revision,
                base_section_revision=current_revision,
                base_markdown=section.markdown,
                section_path=change.section_path,
                replacement_markdown=merged,
                evidence=f"Inverse of applied commit {commit.commit_id}",
                evidence_source="actor_response",
                confidence=1.0,
            )
        )

    for creation in commit.applied_creations:
        path = store.resolve_path(creation.document)
        if not path.exists():
            continue
        current = store.read_document(creation.document)
        if (
            current.revision == creation.revision
            and current.content == creation.content
        ):
            deletions.append(
                DocumentDeletion(
                    document=creation.document,
                    expected_revision=creation.revision,
                    expected_content=creation.content,
                )
            )
            continue
        conflicts.append(
            WikiInverseConflict(
                document=creation.document,
                section_path=(),
                reason="Created document was edited after the commit.",
                before_markdown="",
                after_markdown=creation.content,
                current_markdown=current.content,
            )
        )

    for deletion in commit.applied_deletions:
        path = store.resolve_path(deletion.document)
        if not path.exists():
            creations.append(
                DocumentCreation(
                    document=deletion.document,
                    content=deletion.content,
                    evidence=f"Inverse of applied commit {commit.commit_id}",
                    evidence_source="actor_response",
                    confidence=1.0,
                )
            )
            continue
        current = store.read_document(deletion.document)
        if (
            current.revision == deletion.revision
            and current.content == deletion.content
        ):
            continue
        conflicts.append(
            WikiInverseConflict(
                document=deletion.document,
                section_path=(),
                reason="Deleted document path now contains different content.",
                before_markdown=deletion.content,
                after_markdown="",
                current_markdown=current.content,
            )
        )

    for replacement in commit.applied_replacements:
        path = store.resolve_path(replacement.document)
        if not path.exists():
            conflicts.append(
                WikiInverseConflict(
                    document=replacement.document,
                    section_path=(),
                    reason="Replaced document no longer exists.",
                    before_markdown=replacement.before_content,
                    after_markdown=replacement.after_content,
                    current_markdown="",
                )
            )
            continue
        current = store.read_document(replacement.document)
        if (
            current.revision == replacement.before_revision
            and current.content == replacement.before_content
        ):
            continue
        if (
            current.revision == replacement.after_revision
            and current.content == replacement.after_content
        ):
            replacements.append(
                DocumentReplacement(
                    document=replacement.document,
                    expected_revision=current.revision,
                    expected_content=current.content,
                    replacement_content=replacement.before_content,
                )
            )
            continue
        conflicts.append(
            WikiInverseConflict(
                document=replacement.document,
                section_path=(),
                reason="Replaced document was edited after the commit.",
                before_markdown=replacement.before_content,
                after_markdown=replacement.after_content,
                current_markdown=current.content,
            )
        )

    if conflicts:
        return WikiInversePlan(
            source_commit_id=commit.commit_id,
            status="conflict",
            message="Manual edits overlap the inverse change.",
            patches=patches,
            creations=creations,
            deletions=deletions,
            replacements=replacements,
            conflicts=conflicts,
        )
    if not patches and not creations and not deletions and not replacements:
        return WikiInversePlan(
            source_commit_id=commit.commit_id,
            status="already_reverted",
            message="All audited sections already match their before state.",
        )
    return WikiInversePlan(
        source_commit_id=commit.commit_id,
        status="ready",
        message="The commit can be inverted without overwriting manual edits.",
        patches=patches,
        creations=creations,
        deletions=deletions,
        replacements=replacements,
    )
