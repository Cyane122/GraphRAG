# ================================
# src/wiki/paths.py
#
# Wiki vault 내부의 안전한 thread root 경로를 해석합니다.
#
# Classes
#   - WikiContextError : Wiki thread 식별자 또는 경로 범위가 유효하지 않을 때 발생합니다.
#
# Functions
#   - wiki_thread_root_for_vault(vault_root: Path, thread_id: str) -> Path : 지정 Wiki vault 안의 검증된 thread root를 반환합니다.
# ================================

from __future__ import annotations

from pathlib import Path
import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WikiContextError(RuntimeError):
    """Wiki runtime에 필요한 문서나 메타데이터가 유효하지 않을 때 발생합니다."""


def wiki_thread_root_for_vault(vault_root: Path, thread_id: str) -> Path:
    """Resolve one validated thread root for callers with an explicit vault root."""
    safe_thread_id = _validate_thread_id(thread_id)
    return (vault_root.resolve() / "threads" / safe_thread_id).resolve()


def _validate_thread_id(thread_id: str) -> str:
    """Validate a thread identifier using the Wiki runtime identifier contract."""
    normalized = str(thread_id or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise WikiContextError(f"Invalid thread_id: {thread_id!r}")
    return normalized
