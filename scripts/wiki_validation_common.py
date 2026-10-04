# ================================
# scripts/wiki_validation_common.py
#
# Wiki LLM 검증 스크립트들이 공유하는 임시 vault 격리와 artifact 기록 헬퍼를 제공합니다.
#
# Functions
#   - patch_wiki_roots(roots: WikiRoots) -> None : 앱 모듈의 Wiki world/thread root 참조를 임시 검증 경로로 맞춥니다.
#   - canonical_documents(thread_root: Path) -> dict[str, str] : runtime 산출물을 제외한 canonical Markdown snapshot을 반환합니다.
#   - render_document_diff(before: dict[str, str], after: dict[str, str]) -> str : 문서 snapshot 사이의 unified diff를 렌더링합니다.
#   - write_json(path: Path, payload: object) -> None : UTF-8 JSON artifact를 저장합니다.
# ================================

from __future__ import annotations

import difflib
import json
from pathlib import Path

from src.wiki.paths import WikiRoots


_RUNTIME_MARKDOWN_PARTS = frozenset({"commits", "debug"})


def patch_wiki_roots(roots: WikiRoots) -> None:
    """Point imported app Wiki world and thread roots at isolated validation roots."""
    import src.apps.app.conversation_lifecycle as conversation_lifecycle
    import src.apps.app.runtime as app_runtime
    import src.apps.app.service as app_service
    import src.apps.app.wiki_branching as wiki_branching
    import src.apps.app.wiki_controls as wiki_controls
    import src.apps.app.wiki_message_ops as wiki_message_ops
    import src.apps.app.wiki_service as wiki_service

    app_runtime.WIKI_ROOTS = roots
    app_service.WIKI_ROOTS = roots
    conversation_lifecycle.WIKI_ROOTS = roots
    wiki_branching.WIKI_ROOTS = roots
    wiki_controls.WIKI_ROOTS = roots
    wiki_message_ops.WIKI_ROOTS = roots
    wiki_service.WIKI_ROOTS = roots


def canonical_documents(thread_root: Path) -> dict[str, str]:
    """Return canonical Markdown content keyed by thread-relative path."""
    documents: dict[str, str] = {}
    for path in sorted(thread_root.rglob("*.md")):
        relative = path.relative_to(thread_root)
        if path.name == "commit.md" or _RUNTIME_MARKDOWN_PARTS & set(relative.parts):
            continue
        documents[relative.as_posix()] = path.read_text(encoding="utf-8")
    return documents


def render_document_diff(
    before: dict[str, str],
    after: dict[str, str],
) -> str:
    """Render unified diffs for every created, deleted, or changed document."""
    chunks: list[str] = []
    for document in sorted(set(before) | set(after)):
        previous = before.get(document, "")
        current = after.get(document, "")
        if previous == current:
            continue
        chunks.extend(
            difflib.unified_diff(
                previous.splitlines(),
                current.splitlines(),
                fromfile=f"before/{document}",
                tofile=f"after/{document}",
                lineterm="",
            )
        )
    return "\n".join(chunks) + ("\n" if chunks else "")


def write_json(path: Path, payload: object) -> None:
    """Write one JSON artifact as readable UTF-8 text."""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
