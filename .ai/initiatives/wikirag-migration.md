# WikiRAG Parity And Migration

Status: active

## Objective

Reach user-visible and simulation-semantic parity with the Graph reference while
moving canonical Wiki state to Markdown and preserving safer recovery behavior.

## Priority Order

1. User safety and convenience
2. Actor and Updater prompt quality and observability
3. Core state parity
4. Long-running simulation parity
5. Independent repository extraction

## Scope Rules

- Limit Graph changes to critical fixes, shared-runtime regressions, and
  validation needed to keep it usable as the parity reference.
- Match behavior, state meaning, and recovery guarantees rather than Kuzu
  implementation shape.
- A Graph subsystem may become a Markdown section update, deterministic local
  rule, optional postprocessor, or explicit user control in Wiki.
- If Graph behavior is unsafe or ambiguous, document the mismatch and implement
  the safer Wiki behavior instead of copying it silently.

## Sources Of Truth

- Execution board: `architecture_wiki/TODO.md`
- Detailed backlog: `docs/wiki_v2_todo.md`
- Markdown contract: `docs/wiki_v2_format.md`
- Architecture contract: `architecture_wiki/WikiRAG/`

Any Wiki implementation that changes completion status must update both progress
boards in the same change. Update the affected architecture document when a
state boundary, prompt contract, commit lifecycle, conflict policy, or runtime
flow changes.

## Wiki Invariants

- Markdown is canonical; search indexes, embeddings, and backlinks are
  rebuildable caches.
- Actor-visible Markdown is independently assembled and cannot depend on
  runtime wikilink traversal.
- Hidden labels, metadata, inactive variants, and private truth do not enter the
  Actor prompt.
- Current state is revision-safe and changes through deferred, auditable commits.
- Manual Markdown edits, reroll, edit, delete, inverse, and branching must not
  silently corrupt canonical state.
- Retrieval should select the minimum relevant material within explicit recall
  and token budgets.

## Completion Gate

Joint Graph/Wiki feature development resumes only after:

- required parity-board rows are complete,
- the current five Wiki scenarios pass long-play validation,
- reroll, edit, delete, and manual Markdown edits preserve state predictably,
- the remaining extraction blockers are documented.
