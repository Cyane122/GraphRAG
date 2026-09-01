# ================================
# src/wiki/commit.py
#
# Wiki 변경안을 보류·적용하고 외부 Markdown 변경과 inverse를 같은 감사 이력에 보관합니다.
#
# Classes
#   - WikiCommitError : commit.md 직렬화·상태 오류
#   - PendingCommitExists : 적용되지 않은 commit.md 덮어쓰기 방지 예외
#   - WikiCommitQueue : pending commit.md 저장, 로드, 적용·건너뛰기 관리자
# ================================

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Iterator

from pydantic import ValidationError

from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import (
    AppliedDocumentCreation,
    AppliedDocumentChange,
    AppliedDocumentDeletion,
    AppliedSectionChange,
    PendingWikiCommit,
    SectionPatch,
    WikiInversePlan,
    WikiManualAuditResult,
    WikiDocument,
)
from src.wiki.rollback import plan_applied_commit_inverse
from src.wiki.manual_audit import (
    plan_manual_edit_audit,
    refresh_audit_baseline,
    write_audit_baseline,
)
from src.wiki.store import WikiStore, describe_wiki_commit_failure


_PAYLOAD_HEADER = "## Machine Payload\n\n```json\n"
_PAYLOAD_END = "\n```"
_COMMIT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class WikiCommitError(RuntimeError):
    """commit.md를 안전하게 읽거나 상태 전환하지 못했을 때 발생합니다."""


class PendingCommitExists(WikiCommitError):
    """이전 pending commit.md가 아직 남아 있을 때 발생합니다."""


class WikiCommitQueue:
    """thread vault의 단일 pending commit과 적용 이력을 관리합니다."""

    def __init__(self, store: WikiStore) -> None:
        """Wiki 저장소를 사용해 commit.md를 관리합니다."""
        self.store = store

    def queue(self, commit: PendingWikiCommit) -> Path:
        """다음 사용자 입력까지 적용하지 않을 변경안을 commit.md에 저장합니다."""
        with self._commit_lock():
            path = self.store.resolve_path("commit.md")
            try:
                self.store.create_document("commit.md", self._render(commit))
            except FileExistsError:
                existing = self.load()
                if existing == commit:
                    return path
                if existing and existing.commit_id == commit.commit_id:
                    raise PendingCommitExists(
                        "commit.md has the same commit_id with a different payload"
                    )
                raise PendingCommitExists("commit.md already contains an unapplied Wiki commit")
            return path

    def apply_immediate(self, commit: PendingWikiCommit) -> PendingWikiCommit:
        """새 commit을 기존 pending과 경합 없이 즉시 적용하고 archive로 보관합니다."""
        with self._commit_lock():
            if self.load() is not None:
                raise PendingCommitExists(
                    "Cannot apply an immediate Wiki commit while commit.md is pending"
                )
            self._audit_external_changes_locked()
            self.store.create_document("commit.md", self._render(commit))
            applied = self._apply_pending_locked()
            if applied is None:
                raise WikiCommitError("Immediate Wiki commit disappeared before application")
            return applied

    def load(self) -> PendingWikiCommit | None:
        """현재 commit.md를 읽거나 없으면 None을 반환합니다."""
        path = self.store.resolve_path("commit.md")
        if not path.exists():
            return None
        return self._load_path(path)

    def load_archive(self, commit_id: str) -> PendingWikiCommit:
        """검증된 commit ID의 archive를 읽어 반환합니다."""
        normalized = str(commit_id or "").strip()
        if not _COMMIT_ID_RE.fullmatch(normalized):
            raise WikiCommitError(f"Invalid commit id: {commit_id!r}")
        path = self.store.resolve_path(f"commits/{normalized}.md")
        if not path.is_file():
            raise WikiCommitError(f"Commit archive does not exist: {normalized}")
        return self._load_path(path)

    def find_applied_turn_commit(
        self,
        *,
        user_input: str,
        actor_response: str,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> PendingWikiCommit:
        """메시지 ID를 우선하고 내용 hash를 fallback으로 applied update archive를 찾습니다."""
        user_hash = sha256(user_input.encode("utf-8")).hexdigest()
        actor_hash = sha256(actor_response.encode("utf-8")).hexdigest()
        candidates: list[PendingWikiCommit] = []
        archive_root = self.store.root / "commits"
        if not archive_root.is_dir():
            raise WikiCommitError("Wiki commit archive directory does not exist")
        for path in archive_root.glob("*.md"):
            try:
                commit = self._load_path(path)
            except WikiCommitError:
                continue
            if commit.status != "applied" or commit.operation != "update":
                continue
            ids_match = (
                user_message_id is not None
                and assistant_message_id is not None
                and commit.user_message_id == user_message_id
                and commit.assistant_message_id == assistant_message_id
            )
            commit_has_message_ids = (
                commit.user_message_id is not None
                and commit.assistant_message_id is not None
            )
            hashes_match = (
                commit.user_input_hash == user_hash
                and commit.actor_response_hash == actor_hash
            )
            if ids_match or (not commit_has_message_ids and hashes_match):
                candidates.append(commit)
        if not candidates:
            raise WikiCommitError("Applied Wiki commit for the message pair was not found")
        return max(
            candidates,
            key=lambda commit: commit.applied_at or commit.created_at,
        )

    def plan_inverse(self, commit_id: str) -> WikiInversePlan:
        """Applied archive를 쓰기 없이 검사해 inverse 계획을 반환합니다."""
        commit = self.load_archive(commit_id)
        return plan_applied_commit_inverse(self.store, commit)

    def apply_inverse(self, commit_id: str) -> WikiInversePlan:
        """충돌 없는 applied archive를 새 audited inverse commit으로 원자 적용합니다."""
        with self._commit_lock():
            if self.load() is not None:
                raise PendingCommitExists(
                    "Cannot invert an applied commit while commit.md is pending"
                )
            self._audit_external_changes_locked()
            plan = self.plan_inverse(commit_id)
            if plan.status != "ready":
                return plan
            inverse_commit = PendingWikiCommit(
                user_input_hash=sha256(
                    f"inverse-user:{commit_id}".encode("utf-8")
                ).hexdigest(),
                actor_response_hash=sha256(
                    f"inverse-actor:{commit_id}".encode("utf-8")
                ).hexdigest(),
                updater_model="deterministic-wiki-inverse",
                operation="inverse",
                source_commit_id=commit_id,
                summary=f"Inverse of applied Wiki commit {commit_id}",
                patches=plan.patches,
                creations=plan.creations,
                deletions=plan.deletions,
                replacements=plan.replacements,
            )
            self.store.create_document("commit.md", self._render(inverse_commit))
            applied = self._apply_pending_locked()
            if applied is None:
                raise WikiCommitError("Inverse commit disappeared before application")
            return plan.model_copy(
                update={
                    "status": "applied",
                    "message": "The inverse commit was applied.",
                    "inverse_commit_id": applied.commit_id,
                }
            )

    def _load_path(self, path: Path) -> PendingWikiCommit:
        """지정한 commit Markdown의 기계 payload를 검증해 반환합니다."""
        content = path.read_text(encoding="utf-8")
        payload = self._extract_payload(content)
        try:
            return PendingWikiCommit.model_validate_json(payload)
        except ValidationError as exc:
            raise WikiCommitError(f"Invalid commit.md payload: {exc}") from exc

    def apply_pending(self) -> PendingWikiCommit | None:
        """다음 사용자 입력 직전에 pending patch를 적용하고 commit 이력으로 이동합니다."""
        with self._commit_lock():
            self._audit_external_changes_locked()
            return self._apply_pending_locked()

    def audit_external_changes(self) -> WikiManualAuditResult:
        """외부 canonical Markdown 변경을 별도 applied manual archive로 기록합니다."""
        with self._commit_lock():
            return self._audit_external_changes_locked()

    def skip_pending(self, reason: str = "") -> PendingWikiCommit | None:
        """현재 commit.md를 적용하지 않고 skipped 이력으로 보관합니다."""
        with self._commit_lock():
            commit = self.load()
            if commit is None:
                return None
            pending_path = self.store.resolve_path("commit.md")
            archive_relative = f"commits/{commit.commit_id}.md"
            archive_path = self.store.resolve_path(archive_relative)
            if archive_path.exists():
                archived = self._load_path(archive_path)
                if archived.commit_id == commit.commit_id and archived.status == "skipped":
                    pending_path.unlink()
                    return archived
                raise WikiCommitError(f"Commit archive already exists: {archive_relative}")

            commit.status = "skipped"
            commit.resolution_reason = reason.strip() or "Skipped by player control"
            self.store.create_document(archive_relative, self._render(commit))
            pending_path.unlink()
            return commit

    def _apply_pending_locked(self) -> PendingWikiCommit | None:
        """프로세스 간 commit 잠금 안에서 pending patch를 적용하고 보관합니다."""
        commit = self.load()
        if commit is None:
            return None
        pending_path = self.store.resolve_path("commit.md")
        archive_relative = f"commits/{commit.commit_id}.md"
        archive_path = self.store.resolve_path(archive_relative)
        if archive_path.exists():
            archived = self._load_path(archive_path)
            if archived.commit_id == commit.commit_id and archived.status == "applied":
                refresh_audit_baseline(self.store)
                pending_path.unlink()
                return archived
            raise WikiCommitError(f"Commit archive already exists: {archive_relative}")
        before_markdown = self._capture_before_markdown(commit.patches)
        try:
            with self.store.transaction():
                replaced_documents = self._apply_document_operations(commit)
                self.store.apply_patches(commit.patches)
        except Exception as exc:
            commit.status = "failed"
            commit.failure_reason = describe_wiki_commit_failure(exc)
            self.store.write_document("commit.md", self._render(commit))
            raise

        commit.applied_changes = self._capture_applied_changes(
            commit.patches,
            before_markdown,
        )
        commit.applied_creations = [
            AppliedDocumentCreation(
                document=creation.document,
                revision=document_revision(creation.content),
                content=creation.content,
            )
            for creation in commit.creations
        ]
        commit.applied_deletions = [
            AppliedDocumentDeletion(
                document=deletion.document,
                revision=deletion.expected_revision,
                content=deletion.expected_content,
            )
            for deletion in commit.deletions
        ]
        commit.applied_replacements = [
            AppliedDocumentChange(
                document=before.path,
                before_revision=before.revision,
                after_revision=after.revision,
                before_content=before.content,
                after_content=after.content,
            )
            for before, after in replaced_documents
        ]
        commit.status = "applied"
        commit.applied_at = datetime.now(timezone.utc)
        commit.failure_reason = None
        commit.resolution_reason = None
        try:
            self.store.create_document(archive_relative, self._render(commit))
        except FileExistsError:
            archived = self._load_path(archive_path)
            if archived.commit_id != commit.commit_id or archived.status != "applied":
                raise WikiCommitError(f"Commit archive race: {archive_relative}")
        refresh_audit_baseline(self.store)
        pending_path.unlink()
        return commit

    def _apply_document_operations(
        self,
        commit: PendingWikiCommit,
    ) -> list[tuple[WikiDocument, WikiDocument]]:
        """문서 삭제·전체 교체·생성을 적용하고 archive가 실제로 쓰는 before/after 교체
        목록만 반환합니다. 생성·삭제 결과는 호출자가 `commit.creations`/`commit.deletions`
        원본으로 archive를 만들므로 별도로 누적하지 않습니다. 되돌리기는 이 함수의 책임이
        아니다 — 호출자가 `self.store.transaction()` 안에서 호출해야 하며, 실패 시 보상은
        그 transaction의 undo journal이 담당한다."""
        replaced_documents: list[tuple[WikiDocument, WikiDocument]] = []
        for deletion in commit.deletions:
            current_path = self.store.resolve_path(deletion.document)
            if not current_path.exists():
                continue
            current = self.store.read_document(deletion.document)
            if (
                current.revision != deletion.expected_revision
                or current.content != deletion.expected_content
            ):
                raise WikiCommitError(
                    f"Document changed before deletion: {deletion.document}"
                )
            self.store.delete_document(
                deletion.document,
                deletion.expected_revision,
            )
        for replacement in commit.replacements:
            current = self.store.read_document(replacement.document)
            if (
                current.revision != replacement.expected_revision
                or current.content != replacement.expected_content
            ):
                raise WikiCommitError(
                    f"Document changed before replacement: {replacement.document}"
                )
            updated = self.store.write_document(
                replacement.document,
                replacement.replacement_content,
                expected_revision=replacement.expected_revision,
            )
            replaced_documents.append((current, updated))
        for creation in commit.creations:
            current_path = self.store.resolve_path(creation.document)
            if current_path.exists():
                current = self.store.read_document(creation.document)
                if current.content != creation.content:
                    raise WikiCommitError(
                        f"Created document path already differs: {creation.document}"
                    )
                continue
            self.store.create_document(
                creation.document,
                creation.content,
            )
        return replaced_documents

    def _audit_external_changes_locked(self) -> WikiManualAuditResult:
        """Commit lock 안에서 baseline 밖의 canonical 변경을 manual archive로 기록합니다."""
        plan = plan_manual_edit_audit(self.store)
        if plan.commit is None:
            if plan.result.status == "initialized":
                write_audit_baseline(self.store, plan.baseline)
            return plan.result

        archive_relative = f"commits/{plan.commit.commit_id}.md"
        archive_path = self.store.resolve_path(archive_relative)
        if archive_path.exists():
            archived = self._load_path(archive_path)
            same_manual_change = (
                archived.commit_id == plan.commit.commit_id
                and archived.status == "applied"
                and archived.operation == "manual"
                and archived.applied_changes == plan.commit.applied_changes
                and archived.applied_creations == plan.commit.applied_creations
                and archived.applied_deletions == plan.commit.applied_deletions
                and archived.applied_replacements == plan.commit.applied_replacements
            )
            if not same_manual_change:
                raise WikiCommitError(f"Manual audit archive race: {archive_relative}")
        else:
            self.store.create_document(archive_relative, self._render(plan.commit))
        write_audit_baseline(self.store, plan.baseline)
        return plan.result.model_copy(
            update={
                "status": "recorded",
                "message": "External Markdown changes were recorded as a manual commit.",
                "manual_commit_id": plan.commit.commit_id,
            }
        )

    def _capture_before_markdown(
        self,
        patches: list[SectionPatch],
    ) -> dict[tuple[str, tuple[str, ...]], str]:
        """적용 직전 section 원문을 legacy patch의 audit fallback으로 읽습니다."""
        captured: dict[tuple[str, tuple[str, ...]], str] = {}
        documents = {
            patch.document: self.store.read_document(patch.document)
            for patch in patches
        }
        sections_by_document = {
            path: parse_markdown_sections(document.content)
            for path, document in documents.items()
        }
        for patch in patches:
            section = sections_by_document[patch.document].get(tuple(patch.section_path))
            if section is not None:
                captured[(patch.document, tuple(patch.section_path))] = section.markdown
        return captured

    def _capture_applied_changes(
        self,
        patches: list[SectionPatch],
        before_markdown: dict[tuple[str, tuple[str, ...]], str],
    ) -> list[AppliedSectionChange]:
        """적용 후 section을 읽어 archive용 before/after 변경 기록을 만듭니다."""
        changes: list[AppliedSectionChange] = []
        documents = {
            patch.document: self.store.read_document(patch.document)
            for patch in patches
        }
        sections_by_document = {
            path: parse_markdown_sections(document.content)
            for path, document in documents.items()
        }
        for patch in patches:
            key = (patch.document, tuple(patch.section_path))
            before = patch.base_markdown or before_markdown.get(key)
            after_section = sections_by_document[patch.document].get(
                tuple(patch.section_path)
            )
            if before is None or after_section is None:
                continue
            after = after_section.markdown
            changes.append(
                AppliedSectionChange(
                    document=patch.document,
                    section_path=tuple(patch.section_path),
                    before_revision=document_revision(before),
                    after_revision=document_revision(after),
                    before_markdown=before,
                    after_markdown=after,
                )
            )
        return changes

    @contextmanager
    def _commit_lock(self) -> Iterator[None]:
        """queue와 apply 상태 전환을 프로세스 간 배타적으로 직렬화합니다."""
        lock_path = self.store.root / ".wiki_commit.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _render(commit: PendingWikiCommit) -> str:
        """사람이 읽는 요약과 기계용 JSON payload를 함께 가진 Markdown을 반환합니다."""
        title_by_status = {
            "pending": "# Pending Wiki Commit",
            "failed": "# Failed Wiki Commit",
            "applied": "# Applied Wiki Commit",
            "skipped": "# Skipped Wiki Commit",
        }
        lines = [
            title_by_status[commit.status],
            "",
            f"- Commit ID: `{commit.commit_id}`",
            f"- Status: `{commit.status}`",
            f"- Updater: `{commit.updater_model}`",
            f"- Operation: `{commit.operation}`",
            f"- Attempts: `{commit.updater_attempts}`",
            f"- Changes: `{len(commit.patches)}`",
            f"- Creations: `{len(commit.creations)}`",
            f"- Severed creations: `{len(commit.severed_creations)}`",
            f"- Deletions: `{len(commit.deletions)}`",
            f"- Replacements: `{len(commit.replacements)}`",
            f"- Applied changes: `{len(commit.applied_changes)}`",
            f"- Applied creations: `{len(commit.applied_creations)}`",
            f"- Applied deletions: `{len(commit.applied_deletions)}`",
            f"- Applied replacements: `{len(commit.applied_replacements)}`",
        ]
        if commit.summary:
            lines.extend(["", "## Summary", "", commit.summary.strip()])
        if commit.failure_reason:
            lines.extend(["", "## Failure", "", commit.failure_reason.strip()])
        if commit.resolution_reason:
            lines.extend(["", "## Resolution", "", commit.resolution_reason.strip()])
        if commit.severed_creations:
            lines.extend(["", "## Severed Creations", ""])
            lines.extend(
                f"- `{item.document_id}` ({item.document_type}, owner="
                f"`{item.owner}`): {item.reason}"
                for item in commit.severed_creations
            )
        lines.extend([
            "",
            _PAYLOAD_HEADER.rstrip("\n"),
            commit.model_dump_json(indent=2),
            "```",
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _extract_payload(content: str) -> str:
        """commit.md의 marker 사이 JSON code fence 본문을 반환합니다."""
        start = content.rfind(_PAYLOAD_HEADER)
        if start < 0:
            raise WikiCommitError("commit.md machine payload heading is missing")
        payload_start = start + len(_PAYLOAD_HEADER)
        end = content.find(_PAYLOAD_END, payload_start)
        if end < 0:
            raise WikiCommitError("commit.md machine payload fence is not closed")
        payload = content[payload_start:end].strip()
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WikiCommitError(f"commit.md payload is not valid JSON: {exc}") from exc
        return payload
