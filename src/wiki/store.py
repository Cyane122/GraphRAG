# ================================
# src/wiki/store.py
#
# Wiki V2 Markdown 문서를 안전하게 읽고 섹션 변경을 적용합니다.
#
# Classes
#   - WikiStoreError : Wiki 저장소 작업 실패의 기본 예외
#   - WikiPathError : vault 밖 경로나 Markdown이 아닌 경로 예외
#   - WikiRevisionConflict : 수동 편집으로 revision이 달라진 충돌 예외
#   - WikiStore : vault 범위 확인, 원자적 쓰기, 다중 문서 섹션 적용 저장소
# ================================

from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import tempfile
from threading import RLock

from src.wiki.frontmatter import parse_frontmatter
from src.wiki.markdown import apply_section_patches, document_revision, parse_markdown_sections
from src.wiki.models import SectionPatch, WikiDocument


class WikiStoreError(RuntimeError):
    """Wiki 저장소 작업이 안전하게 완료되지 못했을 때 발생합니다."""


class WikiPathError(WikiStoreError):
    """요청 경로가 vault 밖이거나 Markdown 문서가 아닐 때 발생합니다."""


class WikiRevisionConflict(WikiStoreError):
    """읽은 뒤 수동 편집되어 문서 revision이 달라졌을 때 발생합니다."""


class WikiStore:
    """하나의 thread vault에 대한 Markdown 읽기와 섹션 변경을 관리합니다."""

    def __init__(self, root: Path) -> None:
        """vault 루트를 준비하고 프로세스 내부 쓰기 잠금을 생성합니다."""
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = RLock()

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
            if expected_revision is not None:
                if not path.exists():
                    raise WikiRevisionConflict(f"Document disappeared: {normalized_path}")
                current = path.read_text(encoding="utf-8")
                if document_revision(current) != expected_revision:
                    raise WikiRevisionConflict(f"Document changed: {normalized_path}")
            self._atomic_write(path, content, expected_revision=expected_revision)
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

            written: list[str] = []
            written_revisions: dict[str, str] = {}
            try:
                for path, content in updated_content.items():
                    self._atomic_write(
                        self.resolve_path(path),
                        content,
                        expected_revision=originals[path].revision,
                    )
                    written.append(path)
                    written_revisions[path] = document_revision(content)
            except Exception as exc:
                rollback_errors: list[str] = []
                for path in reversed(written):
                    try:
                        self._atomic_write(
                            self.resolve_path(path),
                            originals[path].content,
                            expected_revision=written_revisions[path],
                        )
                    except (OSError, WikiRevisionConflict) as rollback_exc:
                        rollback_errors.append(f"{path}: {rollback_exc}")
                detail = f"; rollback errors: {rollback_errors}" if rollback_errors else ""
                raise WikiStoreError(f"Wiki commit failed: {exc}{detail}") from exc

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
            return document

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
