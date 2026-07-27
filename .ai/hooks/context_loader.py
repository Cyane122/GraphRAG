# ================================
# .ai/hooks/context_loader.py
#
# Loads current repository coordination context for agent lifecycle hooks.
#
# Functions
#   - _repo_root(cwd: str) -> Path : resolves the repository root from hook input.
#   - _read_text(path: Path) -> str : reads an optional UTF-8 context file.
#   - _referenced_initiative(active_text: str, root: Path) -> str : loads the active initiative.
#   - _build_context(root: Path) -> str : assembles bounded model-visible context.
#   - main() -> int : emits lifecycle-hook JSON for Claude Code or Codex.
# ================================

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_INITIATIVE_PATTERN = re.compile(r"\.ai/initiatives/[A-Za-z0-9._/-]+\.md")


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


def _read_text(path: Path) -> str:
    """Read an optional UTF-8 file and return an empty string when absent."""

    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _referenced_initiative(active_text: str, root: Path) -> str:
    """Load the first initiative path explicitly referenced by active work."""

    match = _INITIATIVE_PATTERN.search(active_text)
    if match is None:
        return ""
    initiative_path = (root / match.group(0)).resolve()
    initiatives_root = (root / ".ai" / "initiatives").resolve()
    if initiatives_root not in initiative_path.parents:
        return ""
    return _read_text(initiative_path)


def _build_context(root: Path) -> str:
    """Assemble active work, routing, and the referenced initiative."""

    active = _read_text(root / ".ai" / "active.md")
    routing = _read_text(root / ".ai" / "routing.md")
    initiative = _referenced_initiative(active, root)
    sections = [
        "Repository coordination context. The user request remains authoritative.",
        f"[Active work]\n{active}" if active else "",
        f"[Routing defaults]\n{routing}" if routing else "",
        f"[Referenced initiative]\n{initiative}" if initiative else "",
    ]
    return "\n\n".join(section for section in sections if section)


def main() -> int:
    """Read hook input and emit additional developer context."""

    payload: dict[str, Any] = json.load(sys.stdin)
    event_name = str(payload.get("hook_event_name", "SessionStart"))
    root = _repo_root(str(payload.get("cwd", ".")))
    output = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": _build_context(root),
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
