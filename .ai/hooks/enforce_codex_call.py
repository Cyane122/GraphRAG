# ================================
# .ai/hooks/enforce_codex_call.py
#
# Applies repository defaults to new Codex MCP calls from Claude Code.
#
# Functions
#   - _project_root(payload: dict[str, Any]) -> Path : resolves the Claude project root.
#   - _developer_context(root: Path) -> str : builds durable Codex startup instructions.
#   - main() -> int : rewrites a new Codex MCP call without changing follow-up calls.
# ================================

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _project_root(payload: dict[str, Any]) -> Path:
    """Resolve the project root from Claude's environment or hook input."""

    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    candidate = configured or str(payload.get("cwd", "."))
    return Path(candidate).resolve()


def _developer_context(root: Path) -> str:
    """Return concise startup instructions for the delegated Codex task."""

    return (
        f"Repository root: {root}\n"
        "Read AGENTS.md, .ai/active.md, .ai/routing.md, and the initiative "
        "referenced by active.md before substantial work. The caller's bounded "
        "task is authoritative. Preserve user changes, validate proportionately, "
        "and do not add world- or scenario-related history to "
        "docs/changelog.md."
    )


def main() -> int:
    """Apply defaults only to the MCP tool that creates a new Codex thread."""

    payload: dict[str, Any] = json.load(sys.stdin)
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input")
    if not tool_name.endswith("__codex") or not isinstance(tool_input, dict):
        return 0

    root = _project_root(payload)
    updated: dict[str, Any] = dict(tool_input)
    updated.setdefault("cwd", str(root))
    updated.setdefault("model", os.environ.get("CODEX_MCP_MODEL", "gpt-5.4"))
    updated.setdefault("approval-policy", "on-request")

    config_value = updated.get("config")
    config = dict(config_value) if isinstance(config_value, dict) else {}
    config.setdefault(
        "model_reasoning_effort",
        os.environ.get("CODEX_MCP_REASONING_EFFORT", "high"),
    )
    updated["config"] = config

    existing = str(updated.get("developer-instructions", "")).strip()
    injected = _developer_context(root)
    updated["developer-instructions"] = (
        f"{existing}\n\n{injected}" if existing else injected
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Applied repository Codex defaults.",
            "updatedInput": updated,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
