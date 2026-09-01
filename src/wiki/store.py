# ================================
# src/wiki/store.py
#
# Wiki V2 Markdown 문서를 안전하게 읽고 섹션 변경을 적용합니다.
#
# Classes
#   - WikiStoreError : Wiki 저장소 작업 실패의 기본 예외
#   - WikiPathError : vault 밖 경로나 Markdown이 아닌 경로 예외
#   - WikiRevisionConflict : 수동 편집으로 revision이 달라진 충돌 예외
#   - _JournalEntry : transaction() undo journal 항목 하나(내부용)
#   - WikiStore : vault 범위 확인, 원자적 쓰기, 다중 문서 섹션 적용,
#     undo journal 기반 transaction을 제공하는 저장소
#
# Functions
#   - describe_wiki_commit_failure(exc: BaseException) -> str : 실패 예외를 compensation_errors까지 포함한 사람이 읽을 문자열로 만듭니다.
# ================================

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Literal

from src.wiki.frontmatter import parse_frontmatter
from src.wiki.markdown import apply_section_patches, document_revision, parse_markdown_sections
from src.wiki.models import SectionPatch, WikiDocument


@dataclass
class _JournalEntry:
    """undo journal 항목 하나 — 되돌리는 데 필요한 최소 정보를 보관합니다."""

    kind: Literal["create", "replace", "delete"]
    path: str
    before_content: str | None
    before_revision: str | None
    after_revision: str | None


class WikiStoreError(RuntimeError):
    """Wiki 저장소 작업이 안전하게 완료되지 못했을 때 발생합니다."""


class WikiPathError(WikiStoreError):
    """요청 경로가 vault 밖이거나 Markdown 문서가 아닐 때 발생합니다."""


class WikiRevisionConflict(WikiStoreError):
    """읽은 뒤 수동 편집되어 문서 revision이 달라졌을 때 발생합니다."""


def describe_wiki_commit_failure(exc: BaseException) -> str:
    """실패 예외를 undo journal 보상 실패 상세까지 포함한 사람이 읽을 문자열로 만듭니다.
    예외 타입이나 `exc.args`는 바꾸지 않는다 — `transaction()`이 붙인
    `compensation_errors` 속성이 있으면(보상 자체가 실패한 경우) 그 상세를 문자열
    끝에 덧붙일 뿐이다. Wiki 커밋 실패를 사람이 읽을 문자열로 바꾸는 모든 지점은
    `str(exc)` 대신 이 함수를 불러야 보상 실패 신호가 소비자마다 흩어지지 않는다."""
    compensation_errors = getattr(exc, "compensation_errors", None)
    if compensation_errors:
        return f"{exc}; document rollback failed: {compensation_errors}"
    return str(exc)


class WikiStore:
    """하나의 thread vault에 대한 Markdown 읽기와 섹션 변경을 관리합니다."""

    def __init__(self, root: Path) -> None:
        """vault 루트를 준비하고 프로세스 내부 쓰기 잠금을 생성합니다."""
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = RLock()
        self._journal: list[_JournalEntry] | None = None

    def resolve_path(self, relative_path: str) -> Path:
        """vault 내부 Markdown 상대 경로를 검증하고 절대 경로로 반환합니다."""
        requested = Path(relative_path)
        if requested.is_absolute() or requested.suffix.lower() != ".md":
            raise WikiPathError(f"Wiki path must be a relative .md file: {relative_path!r}")
        resolved = (self.root / requested).resolve()
        if not resolved.is_relative_to(self.root):
            raise WikiPathError(f"Wiki path escapes the vault: {relative_path!r}")
        return resolved

    def read_document(self, relative_path: str) -> WikiDocument:
        """UTF-8 Markdown 문서를 읽고 content hash revision과 함께 반환합니다."""
        path = self.resolve_path(relative_path)
        content = path.read_text(encoding="utf-8")
        return WikiDocument(
            path=Path(relative_path).as_posix(),
            revision=document_revision(content),
            content=content,
            metadata=parse_frontmatter(content),
        )

    def write_document(
        self,
        relative_path: str,
        content: str,
        expected_revision: str | None = None,
    ) -> WikiDocument:
        """문서를 원자적으로 쓰고 새 revision을 반환합니다."""
        normalized_path = Path(relative_path).as_posix()
        path = self.resolve_path(normalized_path)
        metadata = parse_frontmatter(content)
        with self._write_lock:
            before_content: str | None = None
            if expected_revision is not None:
                if not path.exists():
                    raise WikiRevisionConflict(f"Document disappeared: {normalized_path}")
                current = path.read_text(encoding="utf-8")
                if document_revision(current) != expected_revision:
                    raise WikiRevisionConflict(f"Document changed: {normalized_path}")
                before_content = current
            self._atomic_write(path, content, expected_revision=expected_revision)
            if self._journal is not None and before_content is not None:
                self._journal.append(
                    _JournalEntry(
                        kind="replace",
                        path=normalized_path,
                        before_content=before_content,
                        before_revision=expected_revision,
                        after_revision=document_revision(content),
                    )
                )
        return WikiDocument(
            path=normalized_path,
            revision=document_revision(content),
            content=content,
            metadata=metadata,
        )

    def apply_patches(self, patches: list[SectionPatch]) -> list[WikiDocument]:
        """모든 패치를 검증한 뒤 문서별로 적용하고 실패 시 이미 쓴 문서를 복구합니다."""
        grouped: dict[str, list[SectionPatch]] = defaultdict(list)
        for patch in patches:
            grouped[Path(patch.document).as_posix()].append(patch)
        if not grouped:
            return []

        with self._write_lock:
            originals: dict[str, WikiDocument] = {}
            updated_content: dict[str, str] = {}
            for path, document_patches in grouped.items():
                document = self.read_document(path)
                rebased_patches: list[SectionPatch] = []
                current_sections = parse_markdown_sections(document.content)
                for patch in document_patches:
                    if patch.base_revision == document.revision:
                        rebased_patches.append(patch)
                        continue
                    section = current_sections.get(tuple(patch.section_path))
                    if section is None or patch.base_section_revision is None:
                        raise WikiRevisionConflict(f"Target section changed before commit: {path}")
                    rebased = patch.model_copy(update={"base_revision": document.revision})
                    if document_revision(section.markdown) == patch.base_section_revision:
                        rebased_patches.append(rebased)
                        continue
                    # 크래시가 문서 쓰기와 commit.md 보관 사이에 발생했을 수 있다.
                    # 현재 문서가 동일 patch의 결과라면 중복 적용 없이 완료로 간주한다.
                    if apply_section_patches(document, [rebased]) == document.content:
                        continue
                    raise WikiRevisionConflict(f"Target section changed before commit: {path}")
                if rebased_patches:
                    originals[path] = document
                    candidate = apply_section_patches(document, rebased_patches)
                    parse_frontmatter(candidate)
                    updated_content[path] = candidate

            try:
                with self.transaction():
                    for path, content in updated_content.items():
                        self.write_document(
                            path,
                            content,
                            expected_revision=originals[path].revision,
                        )
            except Exception as exc:
                raise WikiStoreError(
                    f"Wiki commit failed: {describe_wiki_commit_failure(exc)}"
                ) from exc

            return [
                WikiDocument(
                    path=path,
                    revision=document_revision(content),
                    content=content,
                    metadata=parse_frontmatter(content),
                )
                for path, content in updated_content.items()
            ]

    def create_document(self, relative_path: str, content: str) -> WikiDocument:
        """기존 파일을 덮지 않는 원자적 생성으로 새 Markdown 문서를 만듭니다."""
        normalized_path = Path(relative_path).as_posix()
        path = self.resolve_path(normalized_path)
        metadata = parse_frontmatter(content)
        with self._write_lock:
            self._atomic_create(path, content)
            if self._journal is not None:
                self._journal.append(
                    _JournalEntry(
                        kind="create",
                        path=normalized_path,
                        before_content=None,
                        before_revision=None,
                        after_revision=document_revision(content),
                    )
                )
        return WikiDocument(
            path=normalized_path,
            revision=document_revision(content),
            content=content,
            metadata=metadata,
        )

    def delete_document(
        self,
        relative_path: str,
        expected_revision: str,
    ) -> WikiDocument | None:
        """Exact revision 문서를 삭제하고 이미 없으면 멱등적으로 None을 반환합니다."""
        normalized_path = Path(relative_path).as_posix()
        path = self.resolve_path(normalized_path)
        with self._write_lock:
            if not path.exists():
                return None
            document = self.read_document(normalized_path)
            if document.revision != expected_revision:
                raise WikiRevisionConflict(
                    f"Document changed before deletion: {normalized_path}"
                )
            path.unlink()
            if self._journal is not None:
                self._journal.append(
                    _JournalEntry(
                        kind="delete",
                        path=normalized_path,
                        before_content=document.content,
                        before_revision=document.revision,
                        after_revision=None,
                    )
                )
            return document

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """블록 안의 문서 쓰기·생성·삭제·patch 적용을 journal에 기록하고, 예외로 끝나면
        역순으로 보상한 뒤 원래 예외를 그대로 재전파합니다. 중첩 호출은 가장 바깥쪽
        transaction의 journal에 합류할 뿐 별도로 보상하지 않습니다."""
        with self._write_lock:
            if self._journal is not None:
                yield
                return
            journal: list[_JournalEntry] = []
            self._journal = journal
            try:
                yield
            except Exception as exc:
                # 보상 쓰기 자체가 이 journal에 다시 기록되지 않도록 먼저 비운다.
                self._journal = None
                compensation_errors = self._rollback_journal(journal)
                if compensation_errors:
                    exc.compensation_errors = compensation_errors
                raise
            finally:
                self._journal = None

    def _rollback_journal(self, journal: list[_JournalEntry]) -> list[str]:
        """journal 항목을 역순으로 보상하고, 실패한 항목의 오류 메시지를 모아 반환합니다."""
        errors: list[str] = []
        for entry in reversed(journal):
            try:
                if entry.kind == "create":
                    self.delete_document(entry.path, entry.after_revision)
                elif entry.kind == "replace":
                    self.write_document(
                        entry.path,
                        entry.before_content,
                        expected_revision=entry.after_revision,
                    )
                else:  # "delete"
                    current_path = self.resolve_path(entry.path)
                    if current_path.exists():
                        current = self.read_document(entry.path)
                        if current.content != entry.before_content:
                            raise WikiStoreError(
                                f"rollback destination contains different content: {entry.path}"
                            )
                        continue
                    self.create_document(entry.path, entry.before_content)
            except Exception as rollback_exc:
                errors.append(f"{entry.kind} {entry.path}: {rollback_exc}")
        return errors

    @staticmethod
    def _atomic_write(
        path: Path,
        content: str,
        expected_revision: str | None = None,
    ) -> None:
        """최종 revision을 확인한 뒤 임시 파일로 대상을 원자 교체합니다."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            if expected_revision is not None:
                if not path.exists():
                    raise WikiRevisionConflict(f"Document disappeared: {path.name}")
                current = path.read_text(encoding="utf-8")
                if document_revision(current) != expected_revision:
                    raise WikiRevisionConflict(f"Document changed during write: {path.name}")
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _atomic_create(path: Path, content: str) -> None:
        """완성된 임시 파일의 hard link를 만들어 경로를 배타적으로 생성합니다."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.link(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
