# ================================
# src/wiki/explorer.py
#
# Wiki vault 문서를 탐색용 요약 목록으로 나열합니다(Explorer 백엔드).
#
# Classes
#   - WikiDocumentSummary : 한 문서의 탐색용 메타데이터 요약
#
# Functions
#   - list_wiki_documents(vault_root: Path, thread_id: str, world_id: str) -> list[WikiDocumentSummary] : world 자산과 thread 문서를 종류·경로 순으로 나열합니다.
# ================================

from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel

from src.wiki.store import WikiStore
from src.wiki.paths import wiki_thread_root_for_vault

_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")


class WikiDocumentSummary(BaseModel):
    """Explorer 트리에 표시할 한 문서의 요약 메타데이터입니다."""

    scope: str
    path: str
    type: str
    id: str
    title: str
    visibility: list[str]
    owner: str | None = None


def _summarize_root(root: Path, scope: str) -> list[WikiDocumentSummary]:
    """한 vault 루트의 Markdown 문서를 요약 목록으로 반환합니다."""
    if not root.is_dir():
        return []
    store = WikiStore(root)
    summaries: list[WikiDocumentSummary] = []
    for path in sorted(root.rglob("*.md")):
        relative_parts = path.relative_to(root).parts
        if path.name == "commit.md" or "commits" in relative_parts:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            document = store.read_document(relative)
        except Exception:
            # 손상된 문서는 진단(diagnose_wiki_scope)이 별도로 보고한다.
            continue
        if document.metadata is None:
            continue
        title_match = _H1_RE.search(document.content)
        summaries.append(
            WikiDocumentSummary(
                scope=scope,
                path=relative,
                type=document.metadata.type,
                id=document.metadata.id,
                title=title_match.group(1).strip() if title_match else document.metadata.id,
                visibility=list(document.metadata.visibility),
                owner=document.metadata.owner,
            )
        )
    return summaries


def list_wiki_documents(
    vault_root: Path,
    thread_id: str,
    world_id: str,
) -> list[WikiDocumentSummary]:
    """한 대화가 참조하는 world 자산과 thread 문서를 요약 목록으로 반환합니다."""
    root = vault_root.resolve()
    summaries = _summarize_root(root / "worlds" / world_id, "world")
    summaries.extend(
        _summarize_root(wiki_thread_root_for_vault(root, thread_id), "thread")
    )
    summaries.sort(key=lambda summary: (summary.scope, summary.type, summary.path))
    return summaries
