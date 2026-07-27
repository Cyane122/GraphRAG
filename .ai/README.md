# Agent Context Layout

This directory separates stable project context from temporary work:

- `project.md`: stable repository and product facts
- `active.md`: the only persistent pointer to current work
- `routing.md`: model, effort, thread, and permission defaults
- `changelog-policy.md`: history inclusion and exclusion rules
- `initiatives/`: longer-running goals referenced by `active.md`
- `hooks/`: deterministic Claude Code and Codex lifecycle enforcement

Do not duplicate architecture details or backlogs here. Keep temporary one-turn
instructions in the user prompt.

## Hook Activation

- Restart Claude Code after changing `.claude/settings.json`.
- In Codex, open `/hooks`, review the project commands, and trust
  `.codex/hooks.json`. Changed hook commands may require approval again.
- New Claude-to-Codex calls default to the verified `gpt-5.4` fallback and
  `high` effort only when the caller omits those values.
- Override those defaults with explicit MCP inputs or the
  `CODEX_MCP_MODEL` and `CODEX_MCP_REASONING_EFFORT` environment variables.
