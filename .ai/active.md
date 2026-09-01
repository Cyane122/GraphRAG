# Active Work

## Current Initiative

WikiRAG parity and migration.

Read `.ai/initiatives/wikirag-migration.md`.

## Current Priority

Complete required Wiki parity work while keeping Graph mode usable as the
reference for user-visible behavior, state meaning, and recovery guarantees.

Authoritative progress boards:

- `architecture_wiki/TODO.md`
- `docs/wiki_v2_todo.md`

## Current Task

Pinned order (user decision, 2026-08-31). Plans and fork resolutions live in
`.re0/iteration/0.2.0-wiki-parity-gaps/` — do not restate them here.

1. Multi-character update bug fix, steps 1-4 of
   `.re0/iteration/0.2.0-wiki-parity-gaps/BUGFIX-multichar-update.local.md`
   (severable validation → owner-distribution metrics → creation authority
   for scene-active characters → relationship ledger materialization).
   **Done 2026-08-31.** All four landed; `tests/smoke_wiki_v2.py`,
   `smoke_wiki_runtime.py`, and `smoke_wiki_world_contract.py` pass. Both
   parity boards, `Commit and Conflicts.md`, and the changelog are updated.
   Not yet verified against a real LLM turn — that is step 2.
2. Real-play measurement over tens to hundreds of turns, using the owner
   distribution metrics from step 1 and the existing latency/token logs.
   Pre-fix baseline, from `scripts/wiki_change_log.py --all-threads
   --owner-distribution` (277 applied changes): eun_seo 23 memory / 65
   character, sian 2 / 15, lee_haewon 3 / 12. Compare against this.
3. Then G1 / G2 / G5 from the same iteration folder. F1-F4 fork decisions are
   recorded in its `DESIGN.local.md`; G1 (knower add/remove) and G4
   (`rumors/` folder) need their WORKFLOW sections revised to match before
   starting.

Open repository question, found while implementing step 1 (not acted on):
`.gitignore:10,12` ignore `scripts/` and `tests/` wholesale. Eleven files were
force-added at some point and stay tracked, but 19 of 26 files in `tests/` are
not — including every module from the 0.1.1 smoke-suite split
(`smoke_wiki_policy.py`, `wiki_smoke_fixtures.py`, `smoke_wiki_vault.py`, ...)
and `scripts/wiki_change_log.py`. Edits to them are invisible to `git status`
and would be lost on a fresh clone. Decide whether to force-add them before
relying on these gates in review.

## Persistent Task Template

### Goal

### Scope

### Non-goals

### Permissions

### Deliverable

### Acceptance criteria

### Validation

### Status
