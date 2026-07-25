# AGENTS.md

This file is the working guide for Codex and other agents editing this repository.

GraphRAG is a graph-based roleplay simulation engine with a FastAPI web UI (static `frontend/app/` + `src/apps/app/`), Kuzu, and Gemini/Claude. Each user turn reads thread-scoped Kuzu graph state, builds a Fixed / Genre / Dynamic prompt for the Actor LLM, streams the response, then commits graph mutations only after the response is accepted on the next turn.

`CLAUDE.md` is a one-line `@AGENTS.md` import, so this file is the single source of truth for both Codex and Claude Code — update **only `AGENTS.md`** when run commands, environment variables, architecture flow, or file structure change. For the longer architecture narrative see `docs/architecture.md`; for the agent-assisted development sequence and skill catalog see `docs/dev_workflow.md` and `docs/skill_index.md`.

## Wiki-First Development Policy

WikiRAG is the primary product-development target until the Wiki parity milestone is
complete. GraphRAG is the reference implementation for user-visible behavior and
simulation semantics during this period, not a requirement to copy Kuzu-specific
internals.

- Prioritize Wiki work in this order: user safety and convenience, Actor/Updater
  prompt quality, core state parity, long-running simulation parity, then independent
  repository extraction.
- Limit GraphRAG changes to critical fixes, shared-runtime regressions, and validation
  needed to keep it usable as the parity reference. Record non-critical Graph gaps
  without diverting the active Wiki milestone.
- Match behavior, state meaning, and recovery guarantees rather than implementation
  shape. A Graph subsystem may become a Markdown section update, deterministic local
  rule, optional postprocessor, or explicit user control in Wiki.
- Treat `architecture_wiki/TODO.md` as the parity execution board and
  `docs/wiki_v2_todo.md` as the detailed Wiki backlog. Every Wiki implementation that
  changes completion status must update both documents in the same change. Do not
  leave an implemented item unchecked or document an unimplemented item as complete.
- Update the affected `architecture_wiki/WikiRAG/` document when a state boundary,
  prompt contract, commit lifecycle, conflict policy, or runtime flow changes. Update
  `docs/wiki_v2_format.md` when the author-facing Markdown contract changes.
- Actor-visible Markdown remains a self-contained prompt module. Do not rely on
  `[[wikilink]]` traversal at Actor runtime; resolve or remove authoring links during
  compilation and never expose a hidden target's label or metadata.
- If the Graph reference behavior is unsafe or ambiguous, such as editing text after
  its graph mutations were committed, document the mismatch and design the safer Wiki
  behavior instead of copying it silently.

Joint Graph/Wiki feature development resumes only after required rows on the parity
board are complete, the five current Wiki scenarios pass long-play validation, and
reroll/edit/delete plus manual Markdown edits preserve state predictably.

## Run Commands

```bash
# Start the web UI (FastAPI, port 8000)
python -m src.apps.app

# Initialize or rebuild a world schema
python -m src.core.database.schema_builder --world_id <world_id>
python -m src.core.database.schema_builder --world_id <world_id> --scenario_id <scenario_id>

# Start authoring/runtime tools
python -m src.apps.world_editor
python -m src.apps.graph_viewer
python -m src.apps.app

# Utility scripts, when present
python scripts/test_connection.py
python scripts/count_tokens.py
python scripts/cot_test.py
```

Copy `example.env` to `.env` and fill in credentials before running the app.

There is no formal test suite or lint/build step. Use focused smoke checks, `python -m py_compile <changed files>`, and the relevant command above when a change touches runtime behavior.

## Environment

Important `.env` variables:

| Variable | Purpose |
| --- | --- |
| `WORLD_ID` | Active world id, such as `babe_univ`, `rofan`, or `sunghwa_high_school` |
| `MAX_TOKEN` | Actor output token limit, default 12288 |
| `WIKI_VAULT_ROOT` | Wiki V2 Markdown vault root, default `wiki_v2` |
| `WIKI_ACTOR_RECALL_BUDGET` | Max accumulating docs (event/memory/goal/item/secret) in the Actor prompt before recency/structural recall trims; default 24 |
| `WIKI_UPDATER_RECALL_BUDGET` | Max accumulating docs in the Wiki Updater input before recall trims (broader than Actor for high recall); default 48 |
| `WIKI_ACTOR_RECALL_TOKEN_BUDGET` | Estimated token cap for accumulating Actor recall docs after ranking; default 12000 |
| `WIKI_UPDATER_RECALL_TOKEN_BUDGET` | Estimated token cap for accumulating Updater recall docs after ranking; default 32000 |
| `WIKI_MEMORY_DISTORTION` | `true` enables relationship-triggered subjective Memory reinterpretation; default `false` |
| `WIKI_GOSSIP` | `true` enables new-Event witness Memory propagation; default `false` |
| `WIKI_PERSONALITY_DRIFT` | `true` enables durable-trigger entries in the dynamic personality-change ledger; default `false` |
| `WIKI_PREGNANCY` | `true` enables explicitly configured reproductive-state ticking and grounded pregnancy checks; default `false` |
| `LLM_MAX_CONCURRENCY` | Max in-flight async Gemini calls (semaphore); throttles post-processing bursts that cause 429, default 2 |
| `LLM_MAX_RETRIES_429` | Max exponential-backoff retries on 429 RESOURCE_EXHAUSTED, default 4 |
| `IMPERSONATION` | `true` enables PC-as-NPC mode |
| `MANAGER_PLANNER_MODE` | `legacy` / `shadow` / `integrated`; context planner (default `legacy`; integrated is Pro/slower, not adopted; shadow = measurement only) |
| `TURN_EXTRACTOR_MODE` | `legacy` / `shadow` / `unified`; turn extractor (default `legacy`; unified is Pro/slower, not adopted; shadow = measurement only) |
| `MODEL_ACTOR` | Main roleplay LLM, usually Gemini Pro |
| `MODEL_CLASSIFIER` | Scene-only classification model, usually Flash |
| `MODEL_STATE_UPDATER` | Lightweight state extraction model |
| `MODEL_COMPLEX_UPDATER` | Multi-node update model, usually temp=0 |
| `MODEL_EVENT_CREATOR` | Event creation and gossip propagation model |
| `MODEL_PRO_UPDATER` | Judgment-based updater model |
| `MODEL_MANAGER_PLANNER` | Integrated manager planner model |
| `MODEL_TURN_EXTRACTOR` | Integrated turn extractor model |
| `MODEL_OUTPUT_REPAIR` | Output blacklist repair model, usually Flash |
| `MODEL_EMBEDDER` | HuggingFace embedding model, normally KURE-v1 |
| `EMBEDDING_DIM` | Embedding dimension, normally 1024 |
| `GOOGLE_PROJECT_ID` | Vertex AI project id |
| `GOOGLE_CLOUD_LOCATION` | Default Google GenAI Vertex location, default `global` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service account JSON path |
| `ANTHROPIC_API_KEY` | Anthropic direct API key for Claude actor models |
| `ANTHROPIC_CLAUDE_SONNET_MODEL` | Anthropic model id for the Sonnet 4.6 UI option |
| `ANTHROPIC_CLAUDE_SONNET_5_MODEL` | Anthropic model id for the Sonnet 5 UI option |
| `ANTHROPIC_CLAUDE_OPUS_4_6_MODEL` | Anthropic model id for the Opus 4.6 UI option |
| `ANTHROPIC_CLAUDE_OPUS_4_7_MODEL` | Anthropic model id for the Opus 4.7 UI option |
| `ANTHROPIC_CLAUDE_OPUS_4_8_MODEL` | Anthropic model id for the Opus 4.8 UI option |
| `ANTHROPIC_VERTEX_REGION` | Claude-on-Vertex region (defaults to `GOOGLE_CLOUD_LOCATION`=global, the Claude-recommended value) |
| `DEEPSEEK_API_KEY` | DeepSeek API key for DeepSeek actor models |
| `DEEPSEEK_BASE_URL` | DeepSeek Anthropic-compatible endpoint, default `https://api.deepseek.com/anthropic` |
| `DEEPSEEK_V4_PRO_MODEL` | DeepSeek model id for the V4 Pro UI option |
| `HF_TOKEN` | HuggingFace token |
| `HOSTED_UI_ORIGINS` | Comma-separated browser origins allowed to call the local FastAPI engine; defaults to local Sites dev origins plus the production Sites origin |

Environment variables should be read through `src/config.py`. Do not add direct `os.getenv` calls in other modules.

## Architecture

### Database

Kuzu runs in process and does not need a separate server.

- World schemas are initialized with `schema_builder`.
- Each FastAPI Graph conversation has an isolated DB under
  `data/threads/<thread_id>/schema`; `graph/<world_id>` remains the standalone
  schema-builder/default-driver path.
- Driver code lives in `src/core/database/driver.py` with `KuzuAsyncDriver` and `ProxyDriver`.
- `src/core/database/session.py` exposes `KuzuSession` (per-query lock) and `KuzuTransaction` (atomic multi-write; lock held across BEGIN→COMMIT, ROLLBACK on error, non-reentrant). Use `async_driver.transaction()` for grouped writes.
- Thread metadata and conversation state are JSON under `data/threads/<id>.json`, managed by `src/apps/app/storage.py` (`ConversationStore`).
- Usernotes are shared by engine mode and world under `data/worlds/<graph|wiki>/<world_id>/usernotes.json`; Graph and Wiki namespaces must never share worlds or notes implicitly.

### Turn Lifecycle

One accepted roleplay turn flows through these layers:

```text
Web UI user input (src.apps.app.service.append_user_and_stream)
  -> src.apps.app.input_routing
     -> /help, /debug, empty input, OOC-only, reroll/edit/delete routing
  -> src.apps.app.commit.commit_pending_web
     -> previous turn pending DB writes are applied
  -> src.agents.prompt_factory.ooc_handler
     -> *...* OOC-only inputs may mutate DB immediately and end early
  -> src.agents.manager.pipeline.run_manager_pipeline
     -> world bootstrap + global state
     -> scene classification (time is not planned here)
     -> personal fact extraction
     -> integrated/legacy context plan
     -> core context: character, memory, event, relation
     -> dynamic context: goal, item, secret, social
  -> src.agents.prompt_factory.builder
     -> Fixed + Genre + Dynamic prompt assembly
  -> src.apps.app.actor.stream_actor_events / src.agents.actor
     -> Actor response streaming
  -> src.apps.app.output_guard
     -> blacklist checks and optional repair
  -> src.apps.app.pending_store
     -> response is stored as pending; DB writes are deferred
  -> next user turn starts
  -> src.apps.app.commit.commit_pending_web
     -> accepted Actor prose header time is parsed and committed to GlobalState.currentTime
  -> src.simulation.state.updater.update_accepted_turn(mode="graph")
     -> src.simulation.state.graph_apply.apply_graph_actor_response
     -> literal/figurative classification
     -> multi-character state extraction
     -> event creation + embedding
     -> relationship, affinity, personality deltas
     -> goal, item, secret updates
     -> weather, location, and state mutation
     -> needs decay + autonomous action
     -> schedule tick
     -> memory creation, decay, distortion, narrative compression
```

The deferred commit pattern is intentional. Do not write Actor-response side effects directly to Kuzu during generation, because reroll/edit/delete must be able to discard the response without contaminating graph state.

Wiki mode follows a separate Kuzu-free branch in `src.apps.app.wiki_service`:

```text
Web UI user input
  -> apply previous wiki_v2/threads/<thread_id>/commit.md
  -> src.wiki.runtime.build_wiki_prompt_bundle
     -> Fixed world_lore: world/location/organization/selected situation facts/static character sections
     -> Fixed world_specific_prose_prompt: prose.md exactly once
     -> Genre: existing prompt_factory genre/checklist assets
     -> Dynamic: latest scene/current character state + current Actor-owner relationship/memory Markdown + recent story + user input
  -> existing provider-agnostic Actor streaming
  -> Wiki hidden-Secret output guard
     -> exact/normalized private-truth disclosure is repaired when output repair is enabled
     -> unrepaired disclosure is rejected without exposing the secret in the user-facing error
  -> src.simulation.state.updater.update_accepted_turn(mode="wiki")
     -> src.wiki.commit_planner.plan_pending_commit
     -> one configured Pro updater call with retry; every prior validation rejection
        remains in the correction prompt so later attempts cannot regress earlier fixes
     -> exact-quote evidence source validation; Actor prose cannot establish player state,
        while a complete mixed-source scene H2 may cite a separate exact player_evidence quote
     -> character static sections are read-only; current scene changes replace one complete H2
     -> Actor-owner relationship changes append durable natural-language bullets and preserve prior entries
     -> accepted Actor header advances time without regression; date jumps and location changes require player grounding
     -> durable Events and owner-private Memories may become validated canonical Markdown creations linked to the source commit and message pair
     -> accepted header elapsed time deterministically advances the Actor-owner needs vector
     -> optional relationship-triggered Memory distortion, Event-witness gossip, personality ledger, and configured organic state merge into the same pending commit; failures do not block the primary update
     -> Wiki organic state may return an OOC result through the shared TurnUpdateResult channel
     -> write new commit.md without changing canonical documents
  -> next user input applies that commit before prompt assembly
```

`src.apps.app.wiki_controls` exposes Wiki-only status, apply-now, retry, and skip
operations, plus a write-free preview and explicit audited manual migration for
legacy threads missing runtime-owned character state sections. Retry uses the latest
accepted user/Actor pair and never overwrites a normal pending commit; migration also
refuses to run while any `commit.md` exists. Skipped and superseded failed commits
remain archived.
Each thread also keeps an Actor-invisible `.wikirag-audit-baseline.json`. Before a
pending apply, immediate inverse, or migration, external canonical Markdown changes
are archived first as a deterministic applied `operation="manual"` commit. Stable H2
edits retain section-level three-way inverse, structural document edits use exact
whole-document replacement snapshots, and external creations/deletions retain full
content. Internal applied commits refresh the baseline so they are never mislabeled
as external edits.
`src.apps.app.wiki_message_ops` handles reroll, user/assistant edits, variant
activation, and deletion for the latest Wiki turn. Unapplied changes archive the
superseded commit as skipped. A latest applied turn with no downstream messages is
first inverted through its linked commit; non-overlapping manual edits survive the
line-based three-way merge, conflicts write nothing, and failed Actor regeneration
compensates by inverting the inverse commit. Applied middle-history turns remain
immutable in place; `src.apps.app.wiki_branching` copies the Wiki thread, reverses
later message-linked applied commits in the copy, preserves the source thread, and
returns the selected user input as a draft in the reconstructed branch.
`src.apps.app.conversation_lifecycle` owns Wiki conversation rename, archive/restore,
ZIP export, and staged permanent deletion. Export excludes runtime locks and debug
artifacts; deletion must validate the exact thread root and restore both the vault and
conversation JSON if staged cleanup fails.
Newly applied Wiki commit archives retain section-level before/after Markdown and
hashes in `applied_changes`, exact created/deleted document snapshots, and exact
whole-document replacement snapshots for structural manual edits. A created
Event or Memory is removed by inverse only while its full content revision is
unchanged; a manually edited created document conflicts instead of being deleted.
Memory creation authority follows its owner: Actor prose may establish only the
active Actor profile's memory, while player input may establish only the player
profile's memory. Actor prompt compilation includes only Memories whose owner matches
the active NPC profile, while Updater inputs retain all owners. Pre-audit archives
without those snapshots remain ineligible for automatic inverse operations.

`start_state.md` is materialized only when creating a Wiki thread, while
`opening_scene.md` is inserted only as the initial assistant message. Every normal
Wiki turn rereads Markdown before prompt assembly so external edits are visible
without restarting the app.

Wiki turn debug snapshots record `start_state_materialized`,
`start_state_in_dynamic_prompt`, the current scene revision, and every Updater input
document's path/type/revision/visibility in `metadata.json` and `summary.md`.
Successfully materialized new Wiki threads also receive an Actor-invisible
`.wikirag-runtime.json` marker. Commit status and turn debug use it to distinguish
current-runtime threads from pre-marker legacy threads without migrating them
implicitly.

Actor prompt compilation is a hard metadata boundary. Frontmatter, vault paths,
revisions, world/scenario/thread IDs, `thread.md`, and authoring-only profile variant
headings never enter the Actor prompt. Character profiles may use H3 selectors
`common`, `default`, and scenario IDs under one H2; thread materialization keeps
`common` plus the active selector (or `default` fallback), removes selector headings,
and promotes their nested headings. Updater inputs retain paths and revisions because
they are required for validated section patches.

Wiki `world.md` supplies the default `pov_mode`. A `scenario.md` may set an optional
`pov_mode` frontmatter value to override that default only for conversations created
with that scenario.

### Prompt Contract

Actor prompts have three segments:

| Segment | Source | Rule |
| --- | --- | --- |
| Fixed | Policy, world, static character knowledge | Must remain stable across turns for Gemini implicit caching |
| Genre | Scene-type prose rules and few-shot examples | May change when scene classification changes |
| Dynamic | Header, current location/time, graph context, user input, live hints | Rebuilt every turn |

The shared classifier labels are `daily`, `bonding`, `intimate`, `formal`, `tense`,
`conflict`, `vulnerable`, `action`, and `ambient`. Prompt compilation maps
`vulnerable`/legacy `emotional` to `bonding`, legacy `physical` to `action`,
legacy `workplace` to `formal`, and `aegyo` to `daily`, so every selected key has a
non-empty scene prompt asset.

Never put current time, current location, recent events, relationship scores, needs, schedules, memories, or user input into the Fixed segment.

Prompt text and world prose should live in `.md` files under `src/agents/prompt_factory/prompts/` or `src/assets/worlds/<world_id>/prompt/`, not as large Python string constants.

## Project Map

| Area | Responsibility |
| --- | --- |
| `frontend/app/` | Static chat client served by `src/apps/app/` |
| `hosted-ui/` | Sites-hosted editorial chat client; connects from the browser to the local FastAPI engine over JSON + NDJSON |
| `frontend/world_editor.html` | Static world editor client served by `src/apps/world_editor/` |
| `frontend/ppt_viewer.html` | Static graph viewer client served by `src/apps/graph_viewer/` |
| `src/config.py` | Centralized environment variable access |
| `src/agents/actor.py` | Gemini Actor calls and streaming helpers |
| `src/agents/resolver.py` | Autonomous NPC action decisions when needs exceed thresholds |
| `src/agents/context/` | Context planning, generic fetches, graph-data rendering, scene/transient state |
| `src/agents/manager/` | Turn preparation pipeline, planning, classifier, context assembly, effects, POV, world loading |
| `src/agents/prompt_factory/` | Fixed / Genre / Dynamic prompt builder, OOC handling, checklist prompts, user notes, prompt assets |
| `src/assets/worlds/` | World classes, scenarios, character/schema data, world prompt assets |
| `src/core/commit_artifacts.py` | Commit-unit artifact persistence |
| `src/core/state_normalization.py` | State normalization helpers |
| `src/core/database/` | Kuzu driver/session/proxy wrappers, schema builder, records, CRUD helpers; migration DDLs + parser (`MigrationOp`/`migration_ops`) in `migrations.py`, applied via introspection + a `SchemaMigration` ledger in `driver.py` |
| `src/core/embedding/` | HuggingFace KURE-v1 embedding helpers |
| `src/core/llm/` | LLM wrappers in `client.py`: `get_model` routes to a Vertex Gemini wrapper, or a DeepSeek (Anthropic-compatible) wrapper when the model name starts with `deepseek`; shared concurrency semaphore + timeout/429 retry via `_run_generate_with_retries`. Error taxonomy (`errors.py`); JSON extraction |
| `src/core/logging/` | Conversation and prompt debug logging |
| `src/wiki/` | Experimental Wiki V2 Markdown engine: frontmatter + template scaffolds, authoring-profile variant compilation, compiled Actor prompt and hidden-Secret output contracts, evidence-authorized commit planning, deterministic recall/needs, gated long-running postprocessors, revision-safe vault writes, retries, deferred `commit.md`, external-edit baseline/manual audit, explicit legacy-thread state-contract migration, and audited inverse/three-way conflict planning; independent of Kuzu |
| `src/simulation/events/` | StaticEvent condition evaluation and lifecycle |
| `src/simulation/state/` | Mode-aware accepted-turn Updater in `updater.py`; Graph persistence in `graph_apply.py`; shared request/result models plus state, relationship, event, time, audit, and turn extraction |
| `src/simulation/systems/` | Long-running systems: lazy public facades; needs execution in `needs/engine.py` with pure constants in `needs/models.py`; memory decay, social/transient identity, kakao, schedules, goals, items, secrets, and world dynamics with pure organic probability rules in `world_dynamics/organic_models.py` |
| `src/apps/app/` | Main FastAPI web UI on port 8000; generation service in `service.py`; Wiki commit controls, safe historical branching, and conversation lifecycle in `wiki_controls.py`/`wiki_branching.py`/`conversation_lifecycle.py`; Graph/Wiki message mutation in `message_ops.py` and `wiki_message_ops.py`; routes, commit, actor streaming, storage, input routing, output guard/repair, pending store |
| `src/apps/world_editor/` | FastAPI world authoring GUI on port 8765; AST source edits in `source_edit.py`/`source_create.py`, shared pure text/offset+emit helpers in `source_text.py`, DynamicState scalar normalization in `state_normalize.py` |
| `src/apps/graph_viewer/` | Graph viewer backend on port 8766, graph snapshot loading/writing, and static export helpers |
| `architecture_wiki/` | Obsidian architecture vault for separate GraphRAG, WikiRAG, and implementation TODO documentation; never used as WikiRAG runtime state |
| `scripts/` | Development/debug scripts, not production runtime |
| `docs/` | Architecture notes and temporary validation artifacts |

## Where To Change Things

| Task | Start Here |
| --- | --- |
| Web UI turn flow, session state | `src/apps/app/service.py`, `src/apps/app/app.py` |
| GraphRAG/WikiRAG architecture overview and execution board | `architecture_wiki/Home.md` and its linked `Shared/`, `GraphRAG/`, `WikiRAG/`, `Operations/`, `TODO.md` documents |
| Wiki V2 Markdown state, templates/scaffolds, section patches, deferred `commit.md` | `src/wiki/`, `docs/wiki_v2_format.md`, `docs/wiki_v2_todo.md` |
| Reroll, edit, activate variant, delete message | `src/apps/app/message_ops.py`, `src/apps/app/wiki_message_ops.py` |
| Deferred DB commit behavior | `src/apps/app/commit.py`, `src/apps/app/session_models.py`, `src/apps/app/pending_store.py` |
| Input classification and routing | `src/apps/app/input_routing.py`, `src/agents/prompt_factory/ooc_handler.py` |
| Manager orchestration | `src/agents/manager/pipeline.py`, then `src/agents/manager/__init__.py` |
| Scene classification / accepted header time | `src/agents/manager/planning.py`, `src/agents/manager/classifier.py`, `src/simulation/state/apply/time_plan.py` |
| Integrated context planning | `src/agents/manager/integrated_planner.py`, `src/agents/context/planner.py` |
| Core graph context | `src/agents/manager/core_context.py`, `src/agents/context/*` |
| Dynamic world context | `src/agents/manager/world_context.py`, `src/simulation/systems/social/context.py` |
| Prompt rendering | `src/agents/prompt_factory/builder.py`, `fixed.py`, `renderers.py`, `prompt_sections.py` |
| Actor streaming/parsing | `src/apps/app/actor.py`, `src/agents/actor.py` |
| Output blacklist/repair | `src/apps/app/output_guard.py`, `src/apps/app/output_repair.py`, `src/agents/prompt_factory/prompts/blacklist/` |
| State/event updates after acceptance | `src/simulation/state/updater.py`, `graph_apply.py`, `extract/primary.py`, `apply/events.py`, `apply/relationships.py`, `extract/multi_character.py` |
| Integrated turn extraction | `src/simulation/state/turn_extractor.py` |
| Time and location writes | `src/simulation/state/time_plan.py`, `src/core/database/helpers.py` |
| Kuzu schema | `src/assets/worlds/base.py`, `src/core/database/schema_builder.py` |
| New world/scenario | `src/assets/worlds/<world_id>/schema.py`, prompt files, character files |
| Needs/memory/social/kakao/organic systems | `src/simulation/systems/<system>/` or matching flat module |
| LLM wrapper behavior | `src/core/llm/client.py` |
| Conversation persistence | `src/apps/app/storage.py` |
| World editor | `src/apps/world_editor/` |
| Graph viewer | `src/apps/graph_viewer/` |
| Standalone web UI | `src/apps/app/` |

## World And Schema Rules

World implementations extend `src.assets.worlds.base.World`.

For a new world:

1. Create `src/assets/worlds/<world_id>/`.
2. Add `schema.py` with a `World` subclass or module-level `SCENARIOS`.
3. Put character definitions in `characters.py` or a `characters/` package.
4. Put world prompt assets under `prompt/`, usually with `few_shot/`, `scenes/`, and `scenarios/`.
5. Build schema with `python -m src.core.database.schema_builder --world_id <world_id>`.
6. Update the Project Map in `AGENTS.md` (and `docs/architecture.md` if module boundaries changed).

Schema guidance:

- Common node/relationship tables belong in `World._build_tables`.
- World-specific initial data belongs in the world schema or scenario data hook.
- Character static facts go into profile/personality/prompt assets.
- Current mutable facts go into `DynamicState`, `DynamicInformation`, or the relevant system node.
- `Secret.private_summary` must not be rendered directly into Actor prompts; use `public_hint`.
- `Memory` is subjective per character. Do not "fix" memory distortion into objective logs.

## State Update Rules

Accepted Actor responses enter the single mode-aware
`src/simulation/state/updater.py::update_accepted_turn` API. Graph requests continue
through `graph_apply.py`; Wiki requests plan and queue a revision-safe Markdown
commit through `src/wiki/commit_planner.py`.

- Literal physical facts may update `DynamicState`; figurative language must not become injuries or body state.
- Relationship deltas should be conservative. Routine kindness, proximity, repeated intimacy, or politeness are not large affinity milestones.
- Event creation should be reserved for durable narrative changes. Importance 0-1 routine moments should not become persistent events.
- Multi-character extraction belongs in `multi_character.py` and related state modules, not ad hoc inside the web UI service layer.
- Time and location changes should go through `time_plan.py` and database helpers.
- Memory distortion is intentional behavior. Memories may shift in the direction of an NPC's personality; do not "correct" it as a bug without an explicit product change.

## Architecture Rules

- Keep async boundaries async. Web UI handlers, Kuzu session usage, and LLM calls are async-first; do not introduce blocking I/O in turn paths.
- Wrap multi-write or read-modify-write graph operations in `async with async_driver.transaction() as tx:` for atomicity, serialization, and lost-update safety. The driver lock is NOT reentrant: never open a `session()` or call another transaction-using helper from inside a transaction body, and precompute slow calls (e.g. embeddings) before opening the transaction.
- Keep `src/apps/app/app.py` and `service.py` as thin orchestrators. New app-facing backend behavior belongs in `src/apps/app`, `src/apps/graph_viewer`, `src/apps/world_editor`, `src/agents`, `src/simulation`, or `src/core`, then gets called from the relevant backend layer.
- Keep Manager turn preparation mostly side-effect-free. It may classify scene type and prepare context/effects, but in-world time advances from the accepted Actor prose header during deferred commit.
- Use package public APIs where they exist. `src.core.database.__init__` lazily exposes database helpers; `src.agents.manager.__init__` is the Manager public entry point.
- Treat Kuzu writes as persistent simulation state. Validate Actor-derived writes through the existing guard/audit/update paths.
- Long-running systems are best-effort unless the caller explicitly depends on their result. Follow the existing pattern of logging and continuing for gossip, memory distortion, personality drift, goals, items, and secrets.
- Preserve world and thread isolation. Each world lives under `src/assets/worlds/<world_id>`, and each thread gets its own graph path. Do not leak thread/world/scenario-specific assumptions into generic systems.
- Do not move prompt data into Python when a Markdown asset will do. The project deliberately separates prose/data from code.
- Treat every Actor-visible Wiki Markdown body as an independently assembled prompt module. Its body must not refer to another prompt, document, file or scenario; must not name Actor, Updater or prompt/runtime mechanics; and must not defer missing canon or starting facts to a user/player choice. Keep runtime metadata in stripped frontmatter, assign each fact one canonical home, and write concrete content or ask before editing.
- Write Wiki prompt-bearing headings, factual canon and portrayal instructions in English. Korean is limited to proper nouns, titles/honorifics, dialogue or short prose examples, verbatim user source, and parser-required structural headings. `opening_scene.md` remains finished Korean player-facing prose unless the user requests another language.
- Do not store authoring placeholders such as "not decided yet", "determine during play", or "details TBD" in Actor-visible Markdown. Write concrete canon, or ask the user before adding a fact outside your authority. Character ignorance must be modeled as an explicit difference between objective truth and that character's knowledge, not as missing world data.
- Keep domain data models and constants in the owning subsystem's `models.py`, not in higher layers. Example: need-vector constants live in `src/simulation/systems/needs/models.py` (`NeedLevels`, `NEED_DEFAULTS`, `NEED_BASE_RATES`, `SETTLE_LEVELS`), and `agents/resolver.py` imports them downward — avoid the reverse (a simulation module importing constants from `agents`).
- Keep one public accepted-turn Updater for both modes:
  `src.simulation.state.updater.update_accepted_turn`. Extend its mode-aware request
  contract when adding parity behavior; do not create parallel Graph and Wiki
  updater entry files. Storage-specific modules must describe their narrower role,
  such as Graph application or Wiki commit planning, and must remain behind the
  shared Updater.

## Python Style

Follow the `python-dev-style`, `python-refactor-safe`, and `python-file-header` skills for Python edits (see `docs/skill_index.md`). The rules below are the authoritative law.

### File Headers

Every `.py` file starts with the project header block:

```python
# ================================
# src/path/to/file.py
#
# One-line responsibility.
#
# Classes
#   - ClassName : short role
#
# Functions
#   - public_func(arg: Type) -> ReturnType : short role
# ================================
```

Keep headers current whenever you add/remove functions or classes, change public signatures, move a file, or change the file's responsibility. Do not put change-history notes, TODOs, or FIXMEs in the header.

### Docstrings And Comments

- Every function, public or private, needs a docstring.
- Prefer concise docstrings that state behavior and return value.
- Inline comments should explain why a block exists or how a multi-step flow fits together, not restate the code.
- When logic changes, update nearby comments and docstrings in the same patch.

### Typing

- Type all function parameters and return values. Use `-> None` when there is no return.
- Use dataclasses or Pydantic models at module boundaries when a structure has multiple fields and crosses modules.
- Existing dataclass bundles in `src/agents/manager/models.py` and Pydantic models in `src/apps/app/session_models.py` are examples of the preferred boundary style.

### Module Structure

- Split by task/domain, not technology. Keep files that change together near each other.
- `utils` modules are only for low-level reusable helpers. Business logic should have a domain module name.
- Split a file when it exceeds roughly 300 lines and has multiple responsibilities, or when data/prompt content changes independently from logic.
- Merge tiny modules if they always change together and add no useful boundary.
- Avoid new broad import cycles. If a dependency cycle appears, prefer a small model/dependency object or a public `__init__.py` export.

## Database And Filesystem Safety

- Do not delete or rebuild graph data unless the user asked for schema rebuild or destructive cleanup.
- `schema_builder` intentionally deletes the target graph before rebuilding. Mention this when suggesting it.
- Do not use `git reset --hard`, checkout over user edits, or remove files unless explicitly requested.
- The repository may have a dirty worktree. Work with existing edits and avoid unrelated churn.

## Encoding Rules

- All text files must be read and written as UTF-8.
- Never write or modify Korean text through PowerShell. Use patch-based edits, or Python with `encoding="utf-8"` when a script is necessary.
- Prefer patch-based edits over whole-file rewrites.
- Preserve existing Korean text exactly unless the task explicitly asks to modify it.
- After editing Korean text, verify the changed lines by reading them back as UTF-8.

## Frontend Rules

Frontend code includes the small static client under `frontend/app/` (HTML/CSS/JS) served by `src/apps/app`, plus the standalone Sites client under `hosted-ui/`.

- Local chat client: `frontend/app/` (`index.html`, `app.js`, `style.css`). Hosted chat client: `hosted-ui/` (React/vinext). Graph viewer: `frontend/ppt_viewer.html`. World editor UI: `frontend/world_editor.html`.
- The client talks to the FastAPI backend over JSON + NDJSON streaming; keep request/response field names in sync with `src/apps/app/models.py` and the route handlers.
- Do not put simulation rules in frontend components. Frontend components should display or submit state, not decide graph behavior.

## Review Checklist

Before finishing a code change:

- Did the change preserve deferred commit semantics?
- Did Fixed prompt content remain stable and free of turn-specific data?
- Are new Kuzu writes routed through existing helpers or commit paths?
- Are new environment variables routed through `src/config.py`?
- Are headers, docstrings, and type hints updated for changed Python files?
- Are prompt/world data stored as assets instead of large code strings?
- Did you avoid PowerShell writes for Korean text?
- Did you update `AGENTS.md` (and `docs/architecture.md` / `docs/changelog.md` as appropriate) when structure, env, or architecture changed?
- Did you run a focused syntax/smoke check, or explain why not?

## Skills And Workflow

For Python work, follow the user-global skills (see `docs/skill_index.md`):

- `python-file-header` — create/sync the file header block (final step of any Python edit).
- `python-dev-style` — new code / local edits where style or placement is the question.
- `python-refactor-safe` — behavior-preserving refactoring.
- `changelog-maintainer`, `architecture-doc-maintainer`, `portfolio-retrospective-writer`,
  `python-test-review` — documentation, portfolio, and test passes.

For Wiki V2 world and scenario authoring, use the user-global
`author-wikirag-worlds` skill before editing `wiki_v2/worlds/`.

The standard development sequence (inspect → plan → implement → validate → sync
headers → changelog if meaningful → architecture docs if architecture changed) is
described in `docs/dev_workflow.md`. The Python Style rules above remain the
authoritative law for both Codex and Claude Code; the skills elaborate the procedure.

## Codex Review Workflow

After implementing non-trivial changes — especially multi-file edits, refactors,
backend logic, auth, database code, or streaming behavior:

1. Run `/codex:review --background`.
2. Continue with local validation while Codex reviews.
3. Before finalizing, run `/codex:status` and `/codex:result`.
4. Address any serious Codex findings before claiming the task is complete.

For risky design decisions, run:

`/codex:adversarial-review --background challenge the implementation and look for simpler or safer alternatives`

For failing tests or unclear bugs, delegate investigation with:

`/codex:rescue --background investigate the issue and propose the smallest safe fix`
