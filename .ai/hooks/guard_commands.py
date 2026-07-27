# ================================
# .ai/hooks/guard_commands.py
#
# Blocks destructive shell commands before Claude Code or Codex executes them.
#
# Functions
#   - _command(payload: dict[str, Any]) -> str : extracts a shell command from hook input.
#   - _blocked_reason(command: str) -> str | None : identifies prohibited destructive commands.
#   - main() -> int : emits a deny decision when repository safety would be violated.
# ================================

from __future__ import annotations

import json
import re
import sys
from typing import Any

_BLOCKED_COMMANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
        "git reset --hard is forbidden by repository policy.",
    ),
    (
        re.compile(r"\bgit\s+clean\b[^\r\n]*(?:\s|^)-[a-z]*f", re.IGNORECASE),
        "forced git clean is forbidden by repository policy.",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+--\s+(?:\.|\*)", re.IGNORECASE),
        "broad git checkout restoration is forbidden by repository policy.",
    ),
    (
        re.compile(r"\bgit\s+restore\b[^\r\n]*(?:\s|^)(?:\.|\*)", re.IGNORECASE),
        "broad git restore is forbidden by repository policy.",
    ),
    (
        re.compile(
            r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+(?:/|~|\$HOME|\$\{HOME\}|\.|\*)",
            re.IGNORECASE,
        ),
        "broad recursive deletion is forbidden by repository policy.",
    ),
    (
        re.compile(
            r"\bRemove-Item\b[^\r\n]*-Recurse[^\r\n]*(?:\$HOME|~|['\"]?\.['\"]?|\*)",
            re.IGNORECASE,
        ),
        "broad recursive deletion is forbidden by repository policy.",
    ),
)


def _command(payload: dict[str, Any]) -> str:
    """Extract a command string from Claude Code or Codex hook input."""

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    value = tool_input.get("command", tool_input.get("cmd", ""))
    return str(value)


def _blocked_reason(command: str) -> str | None:
    """Return the policy reason for a blocked command, if any."""

    for pattern, reason in _BLOCKED_COMMANDS:
        if pattern.search(command):
            return reason
    return None


def main() -> int:
    """Deny destructive commands and otherwise leave normal permission flow intact."""

    payload: dict[str, Any] = json.load(sys.stdin)
    reason = _blocked_reason(_command(payload))
    if reason is None:
        return 0
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
