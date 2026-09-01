# AGENTS.md

This file defines repository-wide operating rules for Codex and other coding
agents. It is deliberately limited to durable rules.

GraphRAG is a roleplay simulation engine with Graph and Wiki execution modes.
Read `.ai/project.md` for the stable project overview. Before substantial work,
read `.ai/active.md` and any initiative it references. Detailed architecture,
formats, backlogs, workflow, and skill guidance live under `docs/` and
`architecture_wiki/`; load only what the current task needs.

`CLAUDE.md` imports this file and adds Claude Code orchestration rules. Keep
shared repository law here and tool-specific behavior in the owning tool file.

## Instruction Priority

Follow instructions in this order:

1. System, developer, and the user's current request.
2. The nearest applicable nested `AGENTS.md` or `AGENTS.override.md`.
3. This file.
4. `.ai/active.md` and its referenced initiative.
5. Relevant architecture, format, and workflow documentation.
6. Existing implementation behavior.
7. Reasonable inference.

An initiative supplies context and default priorities; it does not broaden the
user's request. When sources conflict, follow the higher-priority source and
report material conflicts instead of silently choosing.

## Working Method

Before a non-trivial change:

1. Read the relevant instructions and documentation.
2. Inspect the implementation, real callers, and current Git state.
3. Identify the smallest coherent change and its non-goals.
4. State material assumptions and plan proportionate validation.
5. Implement without unrelated cleanup.
6. Validate before reporting completion.

Do not infer architecture from filenames alone. Prefer reversible, reviewable
changes. Preserve user edits in a dirty worktree.

## Active Work

`.ai/active.md` is the only persistent pointer to current work. It may reference
a longer-running file under `.ai/initiatives/`.

- Do not place temporary priorities, task status, or migration checklists here.
- Do not assume an initiative is active merely because its document exists.
- For a one-turn request, the user prompt is sufficient; do not rewrite
  `.ai/active.md` unless persistent coordination is useful.
- When implementation changes a Wiki parity board item, update both
  `architecture_wiki/TODO.md` and `docs/wiki_v2_todo.md` in the same change.

## Delegation

Use subagents only when the user requests them or when an applicable instruction
explicitly permits them and the work is genuinely independent.

Good delegation targets include repository exploration, Graph-versus-Wiki
comparison, independent debugging hypotheses, test-gap analysis, and focused
review. Avoid delegation for small sequential tasks or overlapping edits.

Every delegated task must define its goal, scope, permissions, constraints,
non-goals, expected evidence, deliverable, and acceptance criteria. The parent
agent owns final decisions, integration, validation, and completion reporting.
Never allow concurrent edits to overlapping files without an explicit
integration plan.

## Core Architecture Invariants

- Preserve deferred commit semantics: Actor-response side effects must remain
  discardable until the response is accepted.
- Keep async turn paths async and avoid blocking I/O.
- Preserve world, scenario, thread, and Graph/Wiki namespace isolation.
- Route all environment access through `src/config.py`.
- Keep app entry modules and services thin; domain behavior belongs in the
  owning package. LLM provider clients and streaming adapters belong in
  `src/core/llm/`, not `src/apps/app/`.
- Maintain one public accepted-turn entry point:
  `src.simulation.state.updater.update_accepted_turn`.
- Use existing validation, audit, transaction, and commit paths for persistent
  state changes. Wiki commit application rolls back through one
  `WikiStore.transaction()` undo journal; do not add a second compensation
  mechanism.
- Keep turn-specific state out of the Fixed prompt segment.
- Never expose private Secret content, frontmatter, vault paths, revisions,
  thread metadata, or inactive authoring variants to Actor prompts.
- Keep prompt and authored prose in Markdown assets rather than large Python
  constants.

Read `docs/architecture.md` and the affected `architecture_wiki/` documents
before changing these boundaries. Update them when runtime flow, state
ownership, prompt contracts, commit lifecycle, or conflict policy changes.

## User Interface Ownership

`hosted-ui/` is the active user interface. Implement new user-facing screens,
controls, and interaction changes there.

`frontend/app/` is the legacy local client. Do not add features to it. Limit
changes to critical fixes and to keeping it working against the current engine
API. A new control does not need to be mirrored there.

Both clients call the same engine over its JSON API, so a change that only
exposes existing engine behavior is UI-only work and belongs in `hosted-ui/`.
When a feature also needs engine support, keep the engine change in `src/` and
the presentation in `hosted-ui/`; do not move product decisions into the client
or business rules into either client.

`hosted-ui/` is a separate Next.js project with its own Git repository and
package manifest. Do not assume the parent repository's Python toolchain,
tests, or commit state apply to it.

## Graph And Wiki State Rules

- Graph grouped writes use `async with async_driver.transaction() as tx:`.
  The Kuzu lock is non-reentrant: do not open a nested session or call a
  transaction-owning helper from inside a transaction.
- Precompute slow work such as embeddings before opening a transaction.
- Treat Graph writes as persistent simulation state and route Actor-derived
  changes through existing guards and audit paths.
- Wiki canonical Markdown changes are revision-safe and deferred through
  `commit.md`; do not bypass commit planning or overwrite conflicting manual
  edits.
- Actor-visible Wiki Markdown bodies are independently assembled prompt
  modules. They must not depend on runtime wikilink traversal or mention hidden
  runtime mechanics.
- Prompt-bearing Wiki headings and factual instructions are English. Korean is
  limited to proper nouns, honorifics, dialogue, short examples, verbatim source
  text, parser-required headings, and player-facing opening prose.
- Do not store placeholders such as "TBD" or "decide during play" in
  Actor-visible Markdown.
- Memory is subjective; intentional distortion is not an objective-log bug.

For author-facing Markdown contracts, use `docs/wiki_v2_format.md`. For work
under `wiki_v2/worlds/`, use the `author-wikirag-worlds` skill.

## Python Requirements

- Preserve the project file-header block (`# ====`) and keep its path,
  responsibility, classes, functions, and signatures synchronized with the
  file.
- Type every parameter and return value of changed or new functions.
- Give changed or new functions a concise docstring, and update nearby
  comments and docstrings when behavior changes.
- Keep domain logic in owning modules; preserve package ownership and
  dependency direction.
- Avoid `Any` where possible, broad import cycles, speculative abstractions,
  and unrelated cleanup.
- Validate proportionately to the scope and risk of the change.

The development sequence is in `docs/dev_workflow.md`; skill and plugin
boundaries are in `docs/skill_index.md`.

## Safety

- Do not delete or rebuild graph data unless explicitly requested.
- Warn that `schema_builder` deletes its target graph before rebuilding.
- Do not use `git reset --hard`, `git clean -fd`, destructive checkout, broad
  restoration, or unrequested deletion.
- Do not commit, push, merge, rewrite history, or create a pull request unless
  explicitly requested.
- Do not expose secrets, credentials, or private data.
- Do not weaken or delete meaningful validation merely to make a change pass.

## Text And Encoding

- Read and write text as UTF-8.
- Never modify Korean text through PowerShell.
- Prefer patch-based edits and preserve existing Korean text unless the task
  explicitly changes it.
- Read back modified Korean lines to verify encoding and content.

## Changelog Policy

`docs/changelog.md` records meaningful engine, runtime, UI, safety, and
developer-infrastructure changes only.

Never add world- or scenario-related history to the changelog, for either Graph
or Wiki. Excluded material includes world selection, schemas, state surfaces,
migrations, named worlds, scenarios, characters as authored canon, locations,
organizations, lore, prose, opening scenes, profile/content edits, and
authoring-tool or authoring-skill work. Do not mention paths under
`src/assets/worlds/` or `wiki_v2/worlds/`. If an entry's subject is a world or
scenario concern, omit it instead of disguising it as generic engine work.

The executable hook enforces common violations, but the semantic rule above is
authoritative. See `.ai/changelog-policy.md` for examples.

## Validation

Use validation proportionate to the change:

- focused tests when available,
- `python -m py_compile` for changed Python modules,
- relevant smoke scripts,
- broader regression checks for cross-cutting changes,
- representative end-to-end checks for state, prompt, commit, or integration
  work.

When checks cannot run, report what was skipped, why, and the remaining risk.

## Documentation

Update documentation when changing environment variables, run commands,
architecture boundaries, runtime or commit flow, prompt contracts, repository
structure, author-facing Wiki Markdown contracts, or active initiative status.

Do not duplicate detailed architecture or temporary task state in this file.
Do not create one-off planning documents when an existing board or active task
file is the proper home.

## Completion Report

For substantial work, report:

- what changed and why,
- files or components affected,
- validation performed,
- documentation updated,
- remaining risks or follow-up work,
- subagents used and their useful conclusions.
