# ================================
# src/wiki/manual_audit.py
#
# Thread canonical Markdown baseline을 관리하고 외부 편집을 manual commit으로 계획합니다.
#
# Functions
#   - snapshot_audit_baseline(store: WikiStore) -> WikiAuditBaseline : 현재 canonical Markdown snapshot을 만듭니다.
#   - write_audit_baseline(store: WikiStore, baseline: WikiAuditBaseline) -> None : baseline을 원자적으로 저장합니다.
#   - ensure_audit_baseline(store: WikiStore) -> None : baseline이 없는 thread만 현재 상태로 초기화합니다.
#   - refresh_audit_baseline(store: WikiStore) -> None : 현재 canonical 상태로 baseline을 갱신합니다.
#   - plan_manual_edit_audit(store: WikiStore) -> WikiManualAuditPlan : baseline 밖의 외부 변경을 감사 계획으로 만듭니다.
# ================================

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import (
    AppliedDocumentChange,
    AppliedDocumentCreation,
    AppliedDocumentDeletion,
    AppliedSectionChange,
    PendingWikiCommit,
    WikiAuditBaseline,
    WikiAuditBaselineEntry,
    WikiManualAuditPlan,
    WikiManualAuditResult,
)
from src.wiki.store import WikiStore


_BASELINE_NAME = ".wikirag-audit-baseline.json"


def _canonical_paths(store: WikiStore) -> list[str]:
    """Commit/debug 산출물을 제외한 canonical Markdown 상대 경로를 반환합니다."""
    return [
        path.relative_to(store.root).as_posix()
        for path in sorted(store.root.rglob("*.md"))
        if path.name != "commit.md"
        and "commits" not in path.relative_to(store.root).parts
        and "debug" not in path.relative_to(store.root).parts
    ]


def snapshot_audit_baseline(store: WikiStore) -> WikiAuditBaseline:
    """현재 canonical Markdown 전문과 revision을 안정된 경로 순서로 snapshot합니다."""
    documents: dict[str, WikiAuditBaselineEntry] = {}
    for relative_path in _canonical_paths(store):
        document = store.read_document(relative_path)
        documents[relative_path] = WikiAuditBaselineEntry(
            revision=document.revision,
            content=document.content,
        )
    return WikiAuditBaseline(documents=documents)


def _read_audit_baseline(store: WikiStore) -> WikiAuditBaseline | None:
    """저장된 audit baseline을 검증해 반환하고 없으면 None을 반환합니다."""
    path = store.root / _BASELINE_NAME
    if not path.is_file():
        return None
    return WikiAuditBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_audit_baseline(store: WikiStore, baseline: WikiAuditBaseline) -> None:
    """Audit baseline JSON을 같은 thread root에서 원자 교체합니다."""
    path = store.root / _BASELINE_NAME
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{_BASELINE_NAME}.",
        suffix=".tmp",
        dir=store.root,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(baseline.model_dump_json(indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def refresh_audit_baseline(store: WikiStore) -> None:
    """현재 canonical Markdown으로 외부 편집 비교 baseline을 갱신합니다."""
    write_audit_baseline(store, snapshot_audit_baseline(store))


def ensure_audit_baseline(store: WikiStore) -> None:
    """기존 baseline을 보존하고 없는 thread에만 현재 canonical snapshot을 기록합니다."""
    if _read_audit_baseline(store) is None:
        refresh_audit_baseline(store)


def _top_level_section_changes(
    path: str,
    before_content: str,
    after_content: str,
) -> list[AppliedSectionChange] | None:
    """문서 구조가 같으면 변경된 H2 snapshot을, 구조가 다르면 None을 반환합니다."""
    before_sections = parse_markdown_sections(before_content)
    after_sections = parse_markdown_sections(after_content)
    before_h2 = {
        section_path: section
        for section_path, section in before_sections.items()
        if len(section_path) == 1
    }
    after_h2 = {
        section_path: section
        for section_path, section in after_sections.items()
        if len(section_path) == 1
    }
    if tuple(before_h2) != tuple(after_h2):
        return None
    before_start = min((section.start for section in before_h2.values()), default=len(before_content))
    after_start = min((section.start for section in after_h2.values()), default=len(after_content))
    if before_content[:before_start] != after_content[:after_start]:
        return None
    changes: list[AppliedSectionChange] = []
    for section_path, before in before_h2.items():
        after = after_h2[section_path]
        if before.markdown == after.markdown:
            continue
        changes.append(
            AppliedSectionChange(
                document=path,
                section_path=section_path,
                before_revision=document_revision(before.markdown),
                after_revision=document_revision(after.markdown),
                before_markdown=before.markdown,
                after_markdown=after.markdown,
            )
        )
    return changes or None


def _manual_commit(
    before: WikiAuditBaseline,
    after: WikiAuditBaseline,
) -> tuple[PendingWikiCommit, list[str]]:
    """Baseline 차이를 applied manual commit snapshot과 변경 경로로 변환합니다."""
    before_paths = set(before.documents)
    after_paths = set(after.documents)
    changed_paths = sorted(
        before_paths.symmetric_difference(after_paths)
        | {
            path
            for path in before_paths & after_paths
            if before.documents[path].revision != after.documents[path].revision
        }
    )
    section_changes: list[AppliedSectionChange] = []
    document_changes: list[AppliedDocumentChange] = []
    for path in sorted(before_paths & after_paths):
        previous = before.documents[path]
        current = after.documents[path]
        if previous.revision == current.revision:
            continue
        changes = _top_level_section_changes(path, previous.content, current.content)
        if changes is not None:
            section_changes.extend(changes)
            continue
        document_changes.append(
            AppliedDocumentChange(
                document=path,
                before_revision=previous.revision,
                after_revision=current.revision,
                before_content=previous.content,
                after_content=current.content,
            )
        )

    creations = [
        AppliedDocumentCreation(
            document=path,
            revision=after.documents[path].revision,
            content=after.documents[path].content,
        )
        for path in sorted(after_paths - before_paths)
    ]
    deletions = [
        AppliedDocumentDeletion(
            document=path,
            revision=before.documents[path].revision,
            content=before.documents[path].content,
        )
        for path in sorted(before_paths - after_paths)
    ]
    digest_source = json.dumps(
        {
            "before": {path: entry.revision for path, entry in before.documents.items()},
            "after": {path: entry.revision for path, entry in after.documents.items()},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = sha256(digest_source.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    return (
        PendingWikiCommit(
            commit_id=f"manual_{digest[:32]}",
            status="applied",
            created_at=now,
            applied_at=now,
            user_input_hash=sha256(f"manual-user:{digest}".encode("utf-8")).hexdigest(),
            actor_response_hash=sha256(
                f"manual-actor:{digest}".encode("utf-8")
            ).hexdigest(),
            updater_model="external-markdown-audit",
            operation="manual",
            summary="Recorded canonical Markdown changes made outside the Wiki commit queue.",
            applied_changes=section_changes,
            applied_creations=creations,
            applied_deletions=deletions,
            applied_replacements=document_changes,
        ),
        changed_paths,
    )


def plan_manual_edit_audit(store: WikiStore) -> WikiManualAuditPlan:
    """현재 canonical 상태와 baseline 차이를 쓰기 없는 manual audit 계획으로 반환합니다."""
    current = snapshot_audit_baseline(store)
    baseline = _read_audit_baseline(store)
    if baseline is None:
        return WikiManualAuditPlan(
            result=WikiManualAuditResult(
                status="initialized",
                message="The current canonical Markdown will become the audit baseline.",
            ),
            baseline=current,
        )
    if baseline == current:
        return WikiManualAuditPlan(
            result=WikiManualAuditResult(
                status="clean",
                message="No external canonical Markdown changes were detected.",
            ),
            baseline=current,
        )
    commit, changed_paths = _manual_commit(baseline, current)
    return WikiManualAuditPlan(
        result=WikiManualAuditResult(
            status="ready",
            message="External canonical Markdown changes are ready for manual audit.",
            changed_documents=changed_paths,
            manual_commit_id=commit.commit_id,
        ),
        baseline=current,
        commit=commit,
    )
