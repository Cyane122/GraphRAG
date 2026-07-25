# ================================
# src/wiki/diagnostics.py
#
# Wiki vault의 문서 무결성(중복 ID, frontmatter, 섹션 구조)을 스캔합니다.
#
# Classes
#   - WikiDiagnostic : 한 문서에서 발견한 진단 항목
#
# Functions
#   - diagnose_wiki_scope(vault_root: Path, thread_id: str, world_id: str) -> list[WikiDiagnostic] : world 자산과 thread 문서에서 중복 ID·frontmatter·섹션 오류를 수집합니다.
# ================================

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from src.wiki.markdown import parse_markdown_sections
from src.wiki.store import WikiStore


class WikiDiagnostic(BaseModel):
    """Wiki 문서에서 발견한 하나의 무결성 문제입니다."""

    level: Literal["error", "warning"]
    code: Literal["frontmatter", "sections", "duplicate_id"]
    path: str
    message: str


def _scan_root(
    root: Path,
    seen_ids: dict[str, str],
    diagnostics: list[WikiDiagnostic],
) -> None:
    """한 vault 루트의 Markdown을 읽어 진단 항목을 diagnostics에 누적합니다."""
    if not root.is_dir():
        return
    store = WikiStore(root)
    for path in sorted(root.rglob("*.md")):
        relative_parts = path.relative_to(root).parts
        if path.name == "commit.md" or "commits" in relative_parts:
            continue
        relative = path.relative_to(root).as_posix()
        display = f"{root.name}/{relative}"
        try:
            document = store.read_document(relative)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="frontmatter",
                    path=display,
                    message=str(exc)[:200],
                )
            )
            continue
        try:
            parse_markdown_sections(document.content)
        except Exception as exc:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="sections",
                    path=display,
                    message=str(exc)[:200],
                )
            )
        if document.metadata is None:
            continue
        document_id = document.metadata.id
        if document_id in seen_ids:
            diagnostics.append(
                WikiDiagnostic(
                    level="error",
                    code="duplicate_id",
                    path=display,
                    message=(
                        f"Duplicate document id '{document_id}' also declared in "
                        f"{seen_ids[document_id]}"
                    ),
                )
            )
        else:
            seen_ids[document_id] = display


def diagnose_wiki_scope(
    vault_root: Path,
    thread_id: str,
    world_id: str,
) -> list[WikiDiagnostic]:
    """한 대화가 참조하는 world 자산과 thread 문서의 무결성을 진단합니다."""
    root = vault_root.resolve()
    seen_ids: dict[str, str] = {}
    diagnostics: list[WikiDiagnostic] = []
    _scan_root(root / "worlds" / world_id, seen_ids, diagnostics)
    _scan_root(root / "threads" / thread_id, seen_ids, diagnostics)
    return diagnostics
