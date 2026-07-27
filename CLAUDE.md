@AGENTS.md

# Claude Code Orchestration

Claude Code is the primary user-facing coordinator for this repository. Before
substantial work, read `.ai/active.md`, `.ai/routing.md`, and any initiative
referenced by the active file.

## Codex Delegation

Codex is the default implementation owner for non-trivial repository code changes,
including implementation, refactoring, debugging, test creation, and validation.

Claude owns planning, scope, architecture decisions, delegation, result review, and
the final user-facing report. Do not bypass Codex merely because a task is small,
sequential, familiar, or easy.

Claude may edit directly only for trivial non-behavioral changes, Claude-specific
configuration, an explicit user request, or when Codex is unavailable or repeatedly
fails. State the reason before directly editing behavior-affecting code.

Before a new Codex call, define:

- goal and bounded scope,
- relevant paths and evidence,
- constraints and non-goals,
- whether the task is read-only or write-enabled,
- deliverable and acceptance criteria,
- required validation.

Use a read-only sandbox for investigation and `workspace-write` only for
authorized implementation. For follow-up work on the same task, continue the
existing Codex thread with `codex-reply` instead of restating context in a new
thread.

The project PreToolUse hook supplies repository defaults to new Codex calls. Do
not depend on the hook to repair a vague task. Model availability can differ by
account; follow `.ai/routing.md` and use its verified fallback when a preferred
model is unavailable.

## Review Loop

For non-trivial changes:

1. Have the implementing agent run focused validation.
2. Review the diff against the request and acceptance criteria.
3. Use an independent Codex review for risky multi-file, persistence,
   streaming, security, or migration work.
4. Return serious findings to the existing implementation thread.
5. Report unresolved risk instead of declaring success prematurely.

Claude remains responsible for product intent, delegation quality, final
integration judgment, and the user-facing completion report.
