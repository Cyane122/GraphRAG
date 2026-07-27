# Agent And Model Routing

These are defaults, not a substitute for the user's explicit model choice or
the models actually available in the authenticated environment.

## Claude Code

Use the normal strong model for daily coordination, task decomposition,
documentation, bounded planning, and first-pass review.

Escalate to the strongest available reasoning model for:

- high-impact architecture or product decisions,
- ambiguous cross-system reasoning,
- difficult root-cause analysis after a grounded investigation,
- final audit of major milestones,
- repeated failure at a lower effort level.

Claude owns product intent, task boundaries, delegation, and final review. It
should not micromanage implementation details that Codex can resolve from the
repository.

## Codex

Use Codex for repository investigation, implementation, validation,
refactoring, debugging, and diff-oriented review.

Model selection order:

1. An explicit model selected by the user.
2. A model explicitly selected by Claude for a justified task.
3. The authenticated Codex environment's current default.
4. Verified compatibility fallback: `gpt-5.4`.

Do not permanently hard-code an unavailable future model slug. The Claude hook
sets `gpt-5.4` only when a new Codex MCP call omits a model; callers can override
it.

## Reasoning Effort

- `low`: mechanical lookup or narrow summarization
- `medium`: routine bounded implementation
- `high`: migration, architecture, difficult debugging, and serious review
- higher settings: only when supported and the impact or prior failures justify
  the added cost

The shared MCP default is `high`, overridable through the call or the
`CODEX_MCP_REASONING_EFFORT` environment variable.

## Thread And Permission Policy

- Start a new Codex thread for a new bounded task.
- Use `codex-reply` for follow-up implementation, review fixes, or additional
  evidence on that task.
- Use `read-only` for investigation.
- Use `workspace-write` for authorized implementation.
- Keep approval policy at `on-request` unless the user deliberately chooses a
  stricter or more autonomous mode.
