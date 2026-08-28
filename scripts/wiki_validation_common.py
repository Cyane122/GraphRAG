# ================================
# scripts/wiki_validation_common.py
#
# Wiki LLM 검증 스크립트들이 공유하는 임시 vault 격리와 artifact 기록 헬퍼를 제공합니다.
#
# Functions
#   - patch_vault_root(vault_root: Path) -> None : 앱 모듈의 Wiki vault 참조를 임시 검증 경로로 맞춥니다.
#   - canonical_documents(thread_root: Path) -> dict[str, str] : runtime 산출물을 제외한 canonical Markdown snapshot을 반환합니다.
#   - render_document_diff(before: dict[str, str], after: dict[str, str]) -> str : 문서 snapshot 사이의 unified diff를 렌더링합니다.
#   - write_json(path: Path, payload: object) -> None : UTF-8 JSON artifact를 저장합니다.
# ================================

from __future__ import annotations

import difflib
import json
from pathlib import Path


_RUNTIME_MARKDOWN_PARTS = frozenset({"commits", "debug"})


def patch_vault_root(vault_root: Path) -> None:
    """Point imported app Wiki roots at one isolated validation vault."""
    import src.apps.app.conversation_lifecycle as conversation_lifecycle
    import src.apps.app.runtime as app_runtime
    import src.apps.app.service as app_service
    import src.apps.app.wiki_branching as wiki_branching
    import src.apps.app.wiki_controls as wiki_controls
    import src.apps.app.wiki_message_ops as wiki_message_ops
    import src.apps.app.wiki_service as wiki_service
    import src.wiki.paths as wiki_paths

    app_runtime.WIKI_VAULT_ROOT = vault_root
    app_service.WIKI_VAULT_ROOT = vault_root
    conversation_lifecycle.WIKI_VAULT_ROOT = vault_root
    wiki_branching.WIKI_VAULT_ROOT = vault_root
    wiki_controls.WIKI_VAULT_ROOT = vault_root
    wiki_message_ops.WIKI_VAULT_ROOT = vault_root
    wiki_service.WIKI_VAULT_ROOT = vault_root
    wiki_paths.WIKI_VAULT_ROOT = vault_root


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
