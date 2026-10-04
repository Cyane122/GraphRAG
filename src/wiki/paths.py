# ================================
# src/wiki/paths.py
#
# Wiki world 자산 root와 thread 상태 root를 분리해 정의하고 안전한 thread root 경로를 해석합니다.
#
# Classes
#   - WikiContextError : Wiki thread 식별자 또는 경로 범위가 유효하지 않을 때 발생합니다.
#   - WikiRoots : 작성 world root와 물질화 thread root의 겹치지 않는 쌍입니다.
#
# Functions
#   - wiki_thread_root(roots: WikiRoots, thread_id: str) -> Path : threads root의 직속 실제 하위 경로인 검증된 thread root를 반환합니다(junction·symlink 거부).
# ================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from src.config import WIKI_THREADS_ROOT, WIKI_WORLDS_ROOT

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WikiContextError(RuntimeError):
    """Wiki runtime에 필요한 문서나 메타데이터가 유효하지 않을 때 발생합니다."""


@dataclass(frozen=True)
class WikiRoots:
    """Authored world root and materialized thread root, which must not overlap."""

    worlds: Path
    threads: Path

    def __post_init__(self) -> None:
        """Reject a root pair where one root contains the other."""
        worlds = self.worlds.resolve()
        threads = self.threads.resolve()
        if worlds.is_relative_to(threads) or threads.is_relative_to(worlds):
            raise WikiContextError(
                f"Wiki worlds and threads roots must not overlap: {worlds} / {threads}"
            )


# Production root pair. Tests and validation scripts replace module-level copies.
WIKI_ROOTS = WikiRoots(worlds=WIKI_WORLDS_ROOT, threads=WIKI_THREADS_ROOT)


def wiki_thread_root(roots: WikiRoots, thread_id: str) -> Path:
    """Resolve one validated thread root that is a real direct child of the threads root.

    A junction or symlink at the thread path resolves elsewhere and is rejected, whether
    it points outside the threads root or aliases a sibling thread.
    """
    safe_thread_id = _validate_thread_id(thread_id)
    expected = roots.threads.resolve() / safe_thread_id
    if expected.resolve() != expected:
        raise WikiContextError(f"Wiki thread path escapes the threads root: {safe_thread_id}")
    return expected


def _validate_thread_id(thread_id: str) -> str:
    """Validate a thread identifier using the Wiki runtime identifier contract."""
    normalized = str(thread_id or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise WikiContextError(f"Invalid thread_id: {thread_id!r}")
    return normalized
