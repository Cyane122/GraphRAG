# ================================
# .ai/hooks/validate_changelog.py
#
# Prevents world-content and authoring history from entering the changelog.
#
# Functions
#   - _repo_root(cwd: str) -> Path : resolves the repository root from hook input.
#   - _added_lines(root: Path) -> list[str] : returns added changelog lines from the Git diff.
#   - _violations(lines: list[str]) -> list[str] : finds prohibited changelog additions.
#   - main() -> int : blocks completion once when changelog policy is violated.
# ================================

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bworld(?:s|_id|_context|_state)?\b", re.IGNORECASE),
    re.compile(r"\bscenario(?:s|_id)?\b", re.IGNORECASE),
    re.compile(r"(?:월드|세계|시나리오)", re.IGNORECASE),
    re.compile(r"\[콘텐츠\]", re.IGNORECASE),
    re.compile(r"(?:src/assets/worlds|wiki_v2/worlds)/", re.IGNORECASE),
    re.compile(r"\bauthor-wikirag-worlds\b", re.IGNORECASE),
    re.compile(r"\bopening_scene\.md\b", re.IGNORECASE),
    re.compile(r"\bworld author(?:ing)?\b", re.IGNORECASE),
    re.compile(r"(?:월드|세계관)\s*(?:작성|제작|승격|마이그레이션|콘텐츠)", re.IGNORECASE),
)


def _repo_root(cwd: str) -> Path:
    """Resolve the Git root, falling back to the hook working directory."""

    start = Path(cwd or ".").resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return start


def _added_lines(root: Path) -> list[str]:
    """Return added non-header lines from staged and unstaged changelog diffs."""

    added: list[str] = []
    for extra_args in ([], ["--cached"]):
        result = subprocess.run(
            [
                "git",
                "diff",
                *extra_args,
                "--unified=0",
                "--",
                "docs/changelog.md",
            ],
            cwd=root,
            capture_output=True,
            check=False,
            encoding="utf-8",
        )
        added.extend(
            line[1:]
            for line in result.stdout.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
    return list(dict.fromkeys(added))


def _violations(lines: list[str]) -> list[str]:
    """Return prohibited added lines, capped for concise hook feedback."""

    found = [
        line
        for line in lines
        if any(pattern.search(line) for pattern in _FORBIDDEN_PATTERNS)
    ]
    return found[:8]


def main() -> int:
    """Block completion once when added changelog lines violate policy."""

    payload: dict[str, Any] = json.load(sys.stdin)
    if bool(payload.get("stop_hook_active")):
        print("{}")
        return 0

    root = _repo_root(str(payload.get("cwd", ".")))
    violations = _violations(_added_lines(root))
    if not violations:
        print("{}")
        return 0

    details = "\n".join(f"- {line}" for line in violations)
    output = {
        "decision": "block",
        "reason": (
            "docs/changelog.md contains prohibited world-content or authoring "
            "history. Remove or rewrite these additions as generic engine behavior:\n"
            f"{details}"
        ),
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
