# GraphRAG Architecture

Canonical architecture reference for the GraphRAG roleplay simulation engine.
For the short, always-loaded rules see `AGENTS.md` (the source of truth for
working conventions). This document is the longer narrative: how a turn flows,
where state lives, and which design boundaries must not be broken.

For Obsidian navigation and a current execution board, open
`architecture_wiki/` as a separate vault and start at `Home.md`. That vault is
developer documentation only and is never read as WikiRAG runtime state.

> This file supersedes the older `ARCHITECTURE.md`, `ARCHITECTURE_WALKTHROUGH.md`,
> `architecture_analysis.md`, and `architecture_validation.md`, which described the
> retired Chainlit / `src/ui/` layout and have been removed.

---

## 1. System at a glance

GraphRAG is a graph-based roleplay simulation engine:

- **UI:** a static web client under `frontend/app/` (HTML/CSS/JS) served by a
  FastAPI backend in `src/apps/app/` (port 8000), plus a standalone Sites client
  under `hosted-ui/` that connects to the same browser-facing JSON + NDJSON API.
- **Database:** Kuzu, embedded in-process (no separate server). Each thread/chat
  room gets an isolated graph under `data/<world_id>/<thread_id>/`.
- **LLM:** Gemini (Vertex AI) for the Actor and most updaters, with Claude as a
  fallback/option (Vertex or Anthropic direct).
- **Embeddings:** HuggingFace KURE-v1 (1024-dim) via `src/core/embedding/`.

The graph stores "facts about the world": characters, locations, relationships,
memories, needs, schedules, goals, secrets, and items are all nodes and edges.
Each turn reads only the slice of the graph it needs, builds an Actor prompt,
streams a response, and writes graph mutations back **only after** the response
is accepted on the next turn.

### Wiki V2 experimental branch

The `graphRAG/wiki` branch also contains an independent, Kuzu-free foundation in
`src/wiki/`. Markdown is the intended source of truth. H2-and-deeper heading paths
identify replaceable sections, while content hashes protect both whole documents
and target sections from stale writes.

`src/wiki/scaffold.py` creates isolated `worlds/<world_id>` and
`threads/<thread_id>` vaults from Markdown assets in `src/wiki/templates/` without
overwriting existing core documents. YAML frontmatter is loaded into a typed common
metadata boundary, while unknown document-specific keys remain available. The
frontmatter stores `schema_version`, but not a mutable revision counter: revision is
always derived from the complete file hash so an Obsidian save is visible immediately.
The detailed document contract lives in `docs/wiki_v2_format.md`.
World scenarios use `worlds/<world_id>/scenarios/<scenario_id>/`: `scenario.md`
contains only scenario-specific traits and prose rules, `start_state.md` contains
the initial state, and `opening_scene.md` contains only the first-scene prose.

The Wiki Updater reads complete relevant documents and returns only `SectionPatch`
objects. Invalid output is retried. A successful update is written to `commit.md`
without changing target documents; the next player input applies it and archives
the result under `commits/<commit_id>.md`. If the player edited an unrelated section
while the commit was pending, the patch rebases onto the latest document. Editing
the target section itself produces a conflict instead of overwriting the player.

The existing FastAPI app now exposes Wiki worlds as runtime-ready. A new Wiki
conversation materializes `start_state.md` and world character profiles into an
isolated `threads/<thread_id>` vault, inserts `opening_scene.md` as the initial
assistant message, and rereads Markdown on every turn. `src/wiki/runtime.py` adapts
those documents to the existing `PromptBuilder`: stable world/prose/current-situation
facts and character sections become Fixed, common genre/checklist assets remain Genre,
and current state documents plus player input become Dynamic. Before assembly, the
adapter removes frontmatter, paths, revisions, world/scenario/thread IDs, `thread.md`,
and authoring-only profile selector headings. Profiles may select `common` plus an
active H3 variant, falling back to `default`; Actor-visible output contains only the
flattened Markdown inside semantic, path-free XML tags. The Actor streams through
the existing provider-agnostic app path without opening Kuzu. After the Actor response,
the unified Wiki Updater queues `commit.md`; the next player input applies it before
building the new prompt.

`src/apps/app/wiki_controls.py` adds Wiki-only status, apply-now, retry, explicit
regeneration, and skip
operations. The filesystem artifact remains authoritative over the conversation's
display status. A normal pending commit cannot be overwritten by retry; explicit
regeneration archives it as skipped before creating a fresh proposal. Skipped and
superseded failed commits are archived for audit instead of deleted.

`src/apps/app/wiki_message_ops.py` handles reroll, user/assistant edits, response
variant activation, and deletion for the latest Wiki turn while its changes are
still pending, failed, or skipped. Actor regeneration finishes before the existing
`commit.md` is archived, so a generation failure leaves the accepted response and
pending update intact. After successful regeneration or a text/version edit, the old
commit is archived as skipped and a new Updater commit is queued from the active
message pair. Already-applied historical turns are rejected because Wiki does not yet
have inverse patches or a three-way rollback for manual Markdown edits.

Wiki turn debug metadata records whether the selected start state was materialized
into the thread scene and included in Dynamic, together with the scene revision and
the path/type/revision/visibility of every Updater input document.

---

## 2. Turn lifecycle

A single accepted roleplay turn flows through these layers (entry point:
`src/apps/app/service.py::append_user_and_stream`):

1. **Input routing** — `src/apps/app/input_routing.py`
   Handles `/help`, `/debug`, empty input, OOC-only input, and reroll/edit/delete
   routing before any generation.

2. **Deferred commit of the previous turn** — `src/apps/app/commit.py::commit_pending_web`
   The previous turn's pending DB writes are applied now. The accepted Actor prose
   header time is parsed into `GlobalState.currentTime`, and location is reconciled
   from the accepted prose header.

3. **OOC handling** — `src/agents/prompt_factory/ooc_handler.py`
   `*...*` OOC-only inputs may mutate the DB immediately and end the turn early.

4. **Manager pipeline** — `src/agents/manager/pipeline.py::run_manager_pipeline`
   Prepares everything the Actor needs, mostly side-effect-free:
   - world bootstrap + global state (`planning.py`)
   - scene classification (`planning.py`, `classifier.py`) — time is **not** planned here
   - personal fact extraction (`src/simulation/systems/personal_facts.py`)
   - context plan (`integrated_planner.py` or `src/agents/context/planner.py`)
   - core context: character / memory / event / relation (`core_context.py`)
   - dynamic context: goal / item / secret / social (`world_context.py`)

5. **Prompt assembly** — `src/agents/prompt_factory/builder.py`
   Combines the Fixed / Genre / Dynamic segments (see §4).

6. **Actor streaming** — `src/apps/app/actor.py`, `src/agents/actor.py`
   Streams the roleplay response. Nothing is written to Kuzu during generation.

7. **Output guard** — `src/apps/app/output_guard.py` (+ `output_repair.py`)
   Blacklist checks and optional repair.

8. **Pending store** — `src/apps/app/pending_store.py`
   The response is stored as a `PendingCommit`; DB writes are deferred to the
   next turn.

9. **Next turn → state update** — `src/simulation/state/updater.py::update_accepted_turn`
   receives `mode="graph"` and delegates Graph persistence to
   `src/simulation/state/graph_apply.py`.
   When the next turn starts and the previous response is accepted:
   literal/figurative classification, multi-character state extraction, event
   creation + embedding, relationship/affinity/personality deltas, goal/item/secret
   updates, weather/location/state mutation, needs decay + autonomous action,
   schedule tick, and memory creation/decay/distortion/narrative compression.

### Deferred commit (a core invariant)

Actor-response side effects are **never** written to Kuzu during generation.
They are buffered in the pending store and committed on the next turn so that
reroll / edit / delete can discard a response without contaminating graph state.

- **Reroll of the latest (uncommitted) response:** discard pending, regenerate
  from the snapshot, no DB change.
- **Reroll of a past (committed) response:** regenerate text only from the
  context just before the parent input; discard the new pending and restore the
  existing latest pending. The graph stays at its current committed state — the
  user owns any resulting text↔graph divergence.

---

## 3. Database and isolation

- World schemas are initialized once with
  `python -m src.core.database.schema_builder --world_id <world_id>`.
  **This deletes the target graph before rebuilding** — mention that when
  suggesting it.
- Driver: `src/core/database/driver.py` (`KuzuAsyncDriver` + `ProxyDriver`),
  with introspection-based migration and a `SchemaMigration` ledger. Migration
  DDL/column/data ops live in `src/core/database/migrations.py`.
- `src/core/database/session.py` exposes `KuzuSession` (per-query lock) and
  `KuzuTransaction` (atomic multi-write; lock held across BEGIN→COMMIT, rollback
  on error, **non-reentrant**).
- Thread metadata + conversation state are JSON under `data/threads/<id>.json`,
  managed by `src/apps/app/storage.py`.
- Usernotes use a separate mode/world source under
  `data/worlds/<graph|wiki>/<world_id>/usernotes.json`. `ConversationStore`
  hydrates them into a thread when it is loaded, so Graph and Wiki worlds remain
  incompatible even when their textual world IDs match.
- **Thread isolation:** never query across threads; scope the driver with
  `session.py`.

---

## 4. Prompt contract

Actor prompts have three segments:

| Segment | Source | Rule |
| --- | --- | --- |
| **Fixed** | Policy, world, static character knowledge | Must stay identical across turns (Gemini implicit cache hit; Claude uses a `cache_control` breakpoint in `actor.py`) |
| **Genre** | Scene-type prose rules + few-shot | May change when scene classification changes |
| **Dynamic** | Header, current time/location, graph context, user input, live hints | Rebuilt every turn from the graph |

Scene types: `daily`, `emotional`, `physical`, `intimate`, `workplace`, `aegyo`.

Never put current time, location, recent events, relationship scores, needs,
schedules, memories, or user input into the Fixed segment — doing so breaks
caching. Prompt text and world prose belong in `.md` files under
`src/agents/prompt_factory/prompts/` or `src/assets/worlds/<world_id>/prompt/`,
not in large Python string constants.

`ConversationState.ooc_config` remains per-thread. `ConversationState.usernotes`
is a hydrated view of the mode/world-shared usernotes and is injected before the
player input; OOC config is injected after it.

---

## 5. Module map by layer

| Layer | Path | Responsibility |
| --- | --- | --- |
| Config | `src/config.py` | Centralized env access (the only place env vars are read) |
| Agents — Actor | `src/agents/actor.py`, `resolver.py` | Actor calls/streaming; autonomous NPC action when needs exceed thresholds |
| Agents — Context | `src/agents/context/` | Context planning, generic fetches, graph→prompt rendering, scene/transient state |
| Agents — Manager | `src/agents/manager/` | Turn preparation pipeline, planning, classifier, context assembly, effects, POV, queries, world loading |
| Agents — Prompt Factory | `src/agents/prompt_factory/` | Fixed/Genre/Dynamic builder, OOC handling, checklist, user notes, prompt assets |
| Worlds | `src/assets/worlds/` | World base classes, scenarios, per-world schema/characters/prompts |
| Core — Database | `src/core/database/` | Kuzu driver/session/proxy, schema builder, records, CRUD helpers, migrations |
| Core — Embedding | `src/core/embedding/` | KURE-v1 embedding helpers |
| Core — LLM | `src/core/llm/` | Vertex AI wrapper (`client.py`), error taxonomy (`errors.py`), JSON extraction |
| Core — Logging | `src/core/logging/` | Conversation + prompt debug logging |
| Wiki V2 | `src/wiki/` | Kuzu-free Markdown parsing, section patching, revision-safe storage, deferred commit queue, and Wiki commit planning behind the shared Updater |
| Simulation — Events | `src/simulation/events/` | StaticEvent lifecycle + condition evaluation |
| Simulation — State | `src/simulation/state/` | Graph/Wiki mode-aware accepted-turn Updater, Graph application, shared request/result models, `extract/`, and `apply/` |
| Simulation — Systems | `src/simulation/systems/` | Lazy public facades for long-running systems; needs execution lives in `needs/engine.py`, pure need constants in `needs/models.py`, and storage-independent organic probability rules in `world_dynamics/organic_models.py` |
| Apps — Main UI | `src/apps/app/` | FastAPI web UI (port 8000): routes, service, Graph/Wiki commit controls, actor, input routing, output guard/repair, pending store, message ops, storage |
| Apps — World Editor | `src/apps/world_editor/` | World authoring GUI backend (port 8765) |
| Apps — Graph Viewer | `src/apps/graph_viewer/` | Graph viewer backend (port 8766) |
| Frontend | `frontend/` | Static clients: `app/` chat UI, `world_editor.html`, `ppt_viewer.html` |
| Hosted frontend | `hosted-ui/` | Sites-hosted editorial chat room that bridges to the local GraphRAG API |
| Scripts | `scripts/` | Dev/debug only, not production runtime |

---

## 6. Design boundaries (do not break casually)

- **Async only.** Web handlers, Kuzu usage, and LLM calls are async-first. No
  blocking I/O in turn paths; Kuzu work is wrapped in a thread pool.
- **Transactions for multi-write.** Wrap grouped or read-modify-write graph ops in
  `async with async_driver.transaction() as tx:`. The lock is non-reentrant —
  never open a `session()` or call another transaction-using helper inside a
  transaction, and precompute slow calls (e.g. embeddings) beforehand.
- **Fixed segment is immutable across turns.** Any turn-specific content in Fixed
  is a cache-miss bug.
- **Thread/world isolation.** Each world lives under `src/assets/worlds/<world_id>`;
  each thread gets its own graph path. Do not leak thread/world/scenario
  assumptions into generic systems.
- **State lives in the owning subsystem.** Domain models/constants belong in that
  subsystem's `models.py`; higher layers import downward, not the reverse.
- **Memory distortion is intentional.** Memories may drift toward an NPC's
  personality; do not "correct" it as a bug.
- **Prompt/world data stays in Markdown**, not Python string constants.

---

## 7. Where to change things

See the **"Where To Change Things"** table in `AGENTS.md` for a task→file index.
This document explains *why* the boundaries exist; `AGENTS.md` is the quick lookup.
