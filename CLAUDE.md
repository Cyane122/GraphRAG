# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

```bash
# Start the Chainlit web UI (primary entry point)
chainlit run app.py

# Initialize Neo4j schema for a world
python -m src.graph.schemaBuilder

# Utility scripts
python scripts/test_connection.py   # Verify Neo4j connection
python scripts/count_tokens.py      # Token usage analysis
python scripts/cot_test.py          # Chain-of-thought prompt testing
```

Copy `example.env` to `.env` and fill in credentials before running.

**No test suite or build step.** There is no linting config — follow the existing code style.

## Environment Variables (`.env`)

| Variable                      | Purpose                                      |
|-------------------------------|----------------------------------------------|
| `MODEL_ACTOR`                 | Main roleplay LLM (Gemini)                   |
| `MODEL_CLASSIFIER`            | Scene classification LLM                     |
| `MODEL_COMPLEX_UPDATER`       | Multi-node state update LLM                  |
| `MODEL_STATE_UPDATER`         | Lightweight state update LLM                 |
| `NEO4J_URI/USERNAME/PASSWORD` | Graph database connection                    |
| `WORLD_ID`                    | Active world (`babe_univ`, `rofan`, `sses`)  |
| `PERSPECTIVE`                 | `1` = 1st-person, `3` = 3rd-person narrative |
| `MAX_TOKEN`                   | Actor output token limit (~4096)             |
| `IMPERSONATION`               | Feature flag for PC-as-NPC mode              |

## Architecture Overview

This is a **graph-based roleplay simulation engine**. Each user turn passes through a fixed agentic pipeline before and after the main LLM response.

### Request Pipeline (one user turn)

```
User Input (Chainlit)
  → OOC Parser          checks for *...*  out-of-character commands
  → Manager Agent       scene classification + time calculation
  → PromptBuilder       assembles 3-part prompt (Fixed / Genre / Dynamic)
  → Actor Agent         Gemini generates roleplay response
  → Expression Classifier  LITERAL vs FIGURATIVE analysis
  → State Updater       simple Neo4j field writes
  → Complex Updater     multi-node changes, events, affinity deltas
  → Time Manager        in-game time, weather, location
  → Needs Manager       NPC autonomous need tracks; auto-actions at threshold 0.8
  → Memory/Decay Manager  creates memory nodes; distorts old memories over time
  → Pregnancy Manager   menstrual cycle state
  → [deferred DB commit until next turn]
```

### 3-Part Prompt Design

| Part        | Content                                    | Behavior                                   |
|-------------|--------------------------------------------|--------------------------------------------|
| **Fixed**   | Static world/character knowledge           | Cached by Gemini (repeated inference cost) |
| **Genre**   | Scene-type prose rules + few-shot examples | Swapped per scene classification           |
| **Dynamic** | Real-time Neo4j graph context + user input | Rebuilt every turn                         |

Scene types: `daily`, `emotional`, `physical`, `intimate`, `workplace`, `aegyo`

### Deferred Commit Pattern

The previous turn's Neo4j writes are applied at the **start of the next turn**, not immediately after generation. This means a user "reroll" leaves the database in a clean state. See `app.py` for the deferred-commit logic.

## Module Responsibilities

| Module                        | Responsibility                                                         |
|-------------------------------|------------------------------------------------------------------------|
| `src/agents/manager_agent.py` | Orchestrates scene classification, time step, and prompt assembly      |
| `src/agents/actor_agent.py`   | Sends assembled prompt to the actor LLM; handles streaming             |
| `src/prompt/promptBuilder.py` | Builds Fixed + Genre + Dynamic prompt segments; supports 1P/3P         |
| `src/graph/world/`            | World definitions (one class per world, extends `default.py`)          |
| `src/ooc/ooc_parser.py`       | Detects `*...*` commands; extracts forced state changes                |
| `src/updater/`                | All post-generation state mutations (5 files, each a distinct concern) |
| `src/needs/needs_manager.py`  | 6 NPC need tracks (hunger, rest, social, fun, safety, libido)          |
| `src/memory/decay_manager.py` | Memory distortion/compression/deletion based on time and importance    |
| `src/world/world_builder.py`  | Resolves character identities and relationships from Neo4j             |
| `src/utils/db_utils.py`       | Neo4j CRUD; all graph queries go through here                          |
| `src/utils/llm_utils.py`      | Thin wrapper around LLM clients (Gemini, Anthropic, OpenRouter)        |
| `src/utils/embedder.py`       | HuggingFace KURE-v1 embeddings (1024-dim)                              |

## Key Design Constraints

- **Async throughout**: Chainlit, Neo4j async driver, and concurrent LLM calls all require `async/await`. Do not introduce synchronous blocking calls.
- **Gemini implicit caching**: The Fixed prompt segment must remain identical across turns for Gemini's implicit prompt cache to hit. Avoid injecting dynamic content into the Fixed segment.
- **World isolation**: Each world (`WORLD_ID`) has its own Neo4j node labels and world-class. Adding a new world requires a new class in `src/graph/world/` extending `default.py` and a schema init run.
- **Memory nodes scale**: Memory distortion is time-based (in-game days) and importance-weighted (0–10). The decay logic in `decay_manager.py` is intentional — memories are meant to distort toward NPC personality, not remain objective.
