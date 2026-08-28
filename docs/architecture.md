# GraphRAG / WikiRAG Architecture

This is the canonical runtime architecture reference. Repository-wide operating
rules live in `AGENTS.md`; current priorities live in `.ai/active.md`; the detailed
Wiki execution board lives in `architecture_wiki/TODO.md` and
`docs/wiki_v2_todo.md`.

`architecture_wiki/` is a developer-facing Obsidian vault. It is never loaded as
WikiRAG runtime state.

## 1. System Boundaries

The repository contains two roleplay simulation modes behind one FastAPI
application:

- **Graph mode** stores thread-scoped simulation state in embedded Kuzu databases.
- **Wiki mode** stores canonical state as revision-checked Markdown and does not
  depend on Kuzu.
- **Shared runtime** provides conversation storage, input routing, provider-agnostic
  Actor streaming, output guards, and the accepted-turn Updater contract.
- **Hosted UI** lives in `hosted-ui/` and calls the local engine through JSON and
  NDJSON endpoints. It is the active user interface and owns new user-facing work.
- **Local UI** is served from `frontend/app/`. It is the legacy client, kept
  working against the current engine API but no longer receiving new features.

The main backend entry point is `python -m src.apps.app`.

## 2. Persistence And Isolation

### Graph mode

- Each FastAPI conversation owns an isolated database under
  `data/threads/<thread_id>/schema`.
- `graph/<world_id>` remains the standalone schema-builder/default-driver path.
- Conversation metadata is stored in `data/threads/<thread_id>.json` through
  `src/apps/app/storage.py::ConversationStore`.
- Kuzu access lives under `src/core/database/`.
- Grouped writes use `async with async_driver.transaction() as tx:`.
- The transaction lock is non-reentrant. Do not open a nested session or call a
  transaction-owning helper from inside a transaction.

`schema_builder` deletes its selected target before rebuilding it.

### Wiki mode

- Authored source documents live under `wiki_v2/worlds/`.
- Each conversation receives an isolated materialized vault under
  `wiki_v2/threads/<thread_id>/`.
- Canonical Markdown is reread on every normal turn so external edits are visible
  without restarting the server.
- Revisions are content-derived. Pending patches must validate the expected
  document and section revisions before writing.
- `commit.md` is a deferred proposal, not canonical state.

### Shared user notes

User notes are shared by engine mode and world under
`data/worlds/<graph|wiki>/<world_id>/usernotes.json`. Graph and Wiki namespaces
must never share notes implicitly.

## 3. Accepted-Turn Contract

Both modes enter one public state-update API:

`src.simulation.state.updater.update_accepted_turn`

Storage-specific behavior remains behind that boundary:

- Graph requests delegate persistent mutation to
  `src/simulation/state/graph_apply.py`.
- Wiki requests delegate deferred commit planning and queueing to
  `src/simulation/state/wiki_apply.py`; policy validation lives in
  `src/wiki/commit_policy.py`.

Do not create a second public Updater entry point for either mode.

## 4. Graph Turn Lifecycle

The main orchestration path begins at
`src.apps.app.service.append_user_and_stream`:

1. `src.apps.app.input_routing` handles commands, empty input, OOC-only input,
   reroll, edit, and deletion routing.
2. `src.apps.app.commit.commit_pending_web` applies the previously accepted
   pending turn.
3. `src.simulation.state.apply.ooc` coordinates prompt-factory OOC parsing and
   Graph state application without Actor generation.
4. `src.agents.manager.pipeline.run_manager_pipeline` prepares scene
   classification and Graph context.
5. `src.agents.prompt_factory.builder` assembles Fixed, Genre, and Dynamic prompt
   segments.
6. `src.apps.app.actor` streams the Actor response.
7. `src.apps.app.output_guard` validates or repairs the output.
8. `src.apps.app.pending_store` records the response as pending.
9. On the next accepted input, the pending response enters the shared Updater and
   Graph mutations are committed.

### Deferred commit invariant

Actor-response side effects must not be written to Kuzu during generation.
Deferral allows reroll, edit, and deletion to discard unaccepted prose without
contaminating persistent simulation state.

The accepted Actor header is the authority for in-world time advancement.
Manager preparation must not independently advance time.

## 5. Wiki Turn Lifecycle

Wiki mode branches through `src.apps.app.wiki_service`:

1. Apply the previous validated `commit.md`, if present.
2. Build the prompt through `src.wiki.runtime.build_wiki_prompt_bundle`.
3. Stream through the shared provider-agnostic Actor path.
4. Run the hidden-Secret output guard and optional repair.
5. Enter the shared accepted-turn Updater with `mode="wiki"`.
6. Ask one configured Pro Updater to propose section changes, retrying with all
   previous validation feedback preserved.
7. Validate evidence, authority, target sections, and revisions.
8. Merge deterministic needs and optional best-effort long-running systems into
   the same pending proposal.
9. Write a new `commit.md` without changing canonical documents.

The next player input applies the pending proposal before prompt assembly.

### Evidence and ownership

- Actor prose cannot establish player-controlled state.
- Mixed-source scene updates may cite separate exact player evidence.
- Character static sections are read-only at runtime.
- Actor-owned relationship history appends durable natural-language entries.
- Actor prose may establish only the active Actor profile's private Memory.
- Player input may establish only the player profile's private Memory.
- Actor prompt compilation includes only Memories owned by the active NPC;
  Updater inputs retain all owners.

### Audit, manual edits, and inverse operations

Each current-runtime thread has an Actor-invisible `.wikirag-runtime.json` marker
and an Actor-invisible `.wikirag-audit-baseline.json`.

Before pending apply, inverse, or migration, external canonical Markdown changes
are archived as deterministic `operation="manual"` commits. Applied archives
retain enough before/after content and hashes for audited inverse operations:

- Stable section edits use section-level three-way inverse.
- Structural manual edits use exact whole-document snapshots.
- External creation and deletion retain complete document content.
- A created document is removed by inverse only when its current revision still
  matches the archived creation.

Conflicts write nothing.

### Message mutation and branching

`src.apps.app.wiki_message_ops` handles latest-turn reroll, user/assistant edits,
variant activation, and deletion.

- Unapplied proposals are archived as skipped when superseded.
- A latest applied turn with no downstream messages is inverted before mutation.
- Non-overlapping manual edits survive the three-way merge.
- Failed regeneration compensates by inverting the inverse commit.
- Applied middle-history turns are not rewritten in place.
- `src.apps.app.wiki_branching` creates a new conversation, reverses later
  message-linked commits in the copy, preserves the source conversation, and
  returns the selected user input as a draft.

`src.apps.app.conversation_lifecycle` owns rename, archive/restore, ZIP export, and
staged permanent deletion. Destructive lifecycle operations must validate the exact
thread root and restore both vault and conversation JSON if staged cleanup fails.

## 6. Prompt Contract

Actor prompts have three segments:

| Segment | Source | Rule |
| --- | --- | --- |
| Fixed | Policy, world lore, static character knowledge | Stable across turns for implicit caching |
| Genre | Scene prose rules and examples | May change with classification |
| Dynamic | Current time/location/state, selected recall, recent story, user input | Rebuilt every turn |

Supported shared classifier labels are `daily`, `bonding`, `intimate`, `formal`,
`tense`, `conflict`, `vulnerable`, `action`, and `ambient`. Compilation maps legacy
labels to a supported non-empty asset. Wiki world and active-scenario
`scenes/<scene_type>.md` documents may add or override classifier keys through stripped
frontmatter descriptions. Their selected bodies enter Dynamic; inactive bodies and
selection metadata do not enter any Actor segment.

Never put current time, current location, recent events, relationship state, needs,
schedules, memories, or user input into Fixed.

Prompt prose belongs in Markdown assets under
`src/agents/prompt_factory/prompts/` or the relevant world prompt directory, not in
large Python constants.

### Wiki metadata boundary

Actor-visible Wiki Markdown is a self-contained prompt module. Compilation removes:

- YAML frontmatter
- vault paths and revisions
- world, scenario, and thread identifiers
- `thread.md`
- authoring-only selector headings
- hidden target labels and private metadata

Profiles may use `common`, `default`, and scenario selectors under one character
section. Thread materialization retains `common` and the active selector (or
`default` fallback), removes selector headings, and promotes nested headings.
Updater inputs retain paths and revisions because validated patching requires them.

## 7. Ownership Map

| Area | Primary path |
| --- | --- |
| Backend entry and routes | `src/apps/app/` |
| Graph/Wiki generation orchestration | `src/apps/app/service.py`, `src/apps/app/wiki_service.py` |
| Conversation persistence | `src/apps/app/storage.py` |
| Graph/Wiki message operations | `src/apps/app/message_ops.py`, `src/apps/app/wiki_message_ops.py` |
| Wiki controls and branching | `src/apps/app/wiki_controls.py`, `src/apps/app/wiki_branching.py` |
| Conversation lifecycle | `src/apps/app/conversation_lifecycle.py` |
| Actor calls and streaming | `src/apps/app/actor.py` |
| Manager preparation | `src/agents/manager/` |
| Prompt assembly | `src/agents/prompt_factory/` |
| Accepted-turn Updater | `src/simulation/state/updater.py` |
| Graph persistence | `src/simulation/state/graph_apply.py`, `src/core/database/` |
| Long-running systems | `src/simulation/systems/` |
| Wiki runtime and commit planning | `src/wiki/` |
| Chat client (active) | `hosted-ui/` |
| Chat client (legacy) | `frontend/app/` |
| Authoring and graph tools | `src/apps/world_editor/`, `src/apps/graph_viewer/` |

## 8. Design Invariants

- Keep asynchronous turn paths asynchronous.
- Keep app entry modules and services thin.
- Preserve thread, mode, world, and scenario isolation.
- Route environment reads through `src/config.py`.
- Precompute slow external work before opening a Kuzu transaction.
- Keep domain models and constants in their owning subsystem.
- Use package public APIs where they exist.
- Treat persistent Actor-derived writes as validated simulation state.
- Treat optional long-running systems as best-effort unless the caller explicitly
  depends on their result.
- Do not reinterpret subjective Memory distortion as an objective event log.
- Keep frontend components responsible for presentation and transport, not
  simulation rules.
