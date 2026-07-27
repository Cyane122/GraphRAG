# Project Context

## Product

GraphRAG is a graph-based roleplay simulation engine with a FastAPI chat UI,
Kuzu-backed Graph mode, and a Kuzu-free Markdown Wiki mode. Both modes share the
provider-agnostic Actor path and the public accepted-turn Updater boundary while
using different persistence implementations.

Graph mode is the behavioral reference during the current Wiki parity
initiative. This is a product-development relationship, not a requirement to
copy Kuzu internals into Wiki.

## Stable Entry Points

- Main API and local UI: `python -m src.apps.app` (port 8000)
- World editor backend: `python -m src.apps.world_editor` (port 8765)
- Graph viewer backend: `python -m src.apps.graph_viewer` (port 8766)
- Local static chat client: `frontend/app/`
- Sites-hosted client: `hosted-ui/`

## Main Boundaries

- `src/apps/app/`: browser-facing orchestration, storage, message operations,
  Graph/Wiki controls, streaming, and pending commits
- `src/agents/`: Actor, Manager, context selection, and prompt assembly
- `src/simulation/`: accepted-turn updates and long-running simulation systems
- `src/core/`: configuration, database, LLM, embedding, logging, and shared
  persistence infrastructure
- `src/wiki/`: Markdown parsing, compilation, recall, commit planning,
  revision-safe writes, audit, inverse operations, and Wiki postprocessors
- `architecture_wiki/`: developer architecture vault and parity execution board;
  never runtime state
- `wiki_v2/`: Wiki authoring/runtime vault data

## Persistent Invariants

- Actor-response mutations are deferred until acceptance.
- Fixed prompts contain no turn-specific state.
- Graph/Wiki and thread namespaces remain isolated.
- Wiki Markdown is canonical; indexes are rebuildable derivatives.
- Actor prompt compilation strips runtime metadata and hidden information.
- The public accepted-turn API is
  `src.simulation.state.updater.update_accepted_turn`.

## Canonical Documentation

- Architecture: `docs/architecture.md` and `architecture_wiki/`
- Wiki authoring contract: `docs/wiki_v2_format.md`
- Wiki backlog: `docs/wiki_v2_todo.md`
- Parity board: `architecture_wiki/TODO.md`
- Development workflow: `docs/dev_workflow.md`
- Skill routing: `docs/skill_index.md`
- Meaningful history: `docs/changelog.md`
