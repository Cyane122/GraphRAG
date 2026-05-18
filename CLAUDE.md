# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run

```bash
chainlit run app.py                        # main entry
python -m src.core.database.schema_builder # init Kuzu schema (new world/thread)
python scripts/test_connection.py          # verify DB/LLM
python scripts/count_tokens.py             # token analysis
```

`cp example.env .env` → fill creds. No tests, no lint.

## Env (`.env`)

| Var | Purpose |
|-----|---------|
| `MODEL_ACTOR` | roleplay LLM (Gemini Pro) |
| `MODEL_CLASSIFIER` | scene/time classify (Flash) |
| `MODEL_STATE_UPDATER` | lightweight state extract |
| `MODEL_COMPLEX_UPDATER` | multi-node update (temp=0) |
| `MODEL_EVENT_CREATOR` | event gen + gossip |
| `MODEL_PRO_UPDATER` | judgment-based update |
| `MODEL_CLASSIFIER_FALLBACK` | OpenRouter fallback |
| `MODEL_EMBEDDER` | HF KURE-v1 (1024-dim) |
| `GOOGLE_PROJECT_ID` | Vertex AI project |
| `GOOGLE_APPLICATION_CREDENTIALS` | service account JSON path |
| `WORLD_ID` | `babe_univ` / `rofan` / `sses` / `sunghwa_high_school` … |
| `PERSPECTIVE` | `1`=1인칭 `3`=3인칭 |
| `MAX_TOKEN` | actor 출력 상한 (~4096) |
| `IMPERSONATION` | PC→NPC 모드 flag |
| `OPENROUTER_API_KEY` / `HF_TOKEN` / `CHAINLIT_AUTH_SECRET` | 각 서비스 인증 |

## Architecture

Graph-based roleplay sim. 턴마다 고정 파이프라인 실행.

### DB: Kuzu (in-process, 서버 불필요)

- thread별 독립 DB: `data/threads/{thread_id}/schema/`
- driver: `src/core/database/driver.py` (KuzuAsyncDriver)
- thread 메타: JSON → `src/core/data_layer/json_data_layer.py`

### Turn Pipeline

```
Input
→ Router          /help /debug, edit/reroll/delete
→ Deferred Commit 이전 턴 pending DB write 적용
→ OOC Parser      *...* → 즉시 DB 반영
→ Manager (src/agents/manager/pipeline.py)
    ├ world bootstrap + global state
    ├ scene classify + time plan (LLM)
    ├ personal fact extract
    ├ core ctx: char/memory/event/relation
    └ dynamic ctx: goal/item/secret/social
→ PromptBuilder   Fixed+Genre+Dynamic 조합
→ Actor (Gemini)  스트리밍 응답
→ PendingCommit   응답 저장; DB write는 다음 턴으로 defer
→ [next turn] StateUpdater
    ├ LITERAL/FIGURATIVE classify
    ├ multi-char state extract
    ├ event create + embed
    ├ relation/affinity/personality △
    ├ goal/item/secret update
    ├ time/weather/location mutate
    ├ needs decay + auto-action check
    └ memory create + decay distort
```

### 3-Part Prompt

| Part | Content | Rule |
|------|---------|------|
| **Fixed** | policy + rules + world + char | 턴 간 동일 유지 → Gemini implicit cache hit |
| **Genre** | prose rules + few-shot | scene type별 교체 |
| **Dynamic** | header + loc + ctx + input + hints | 매 턴 graph에서 재조립 |

Scene types: `daily` `emotional` `physical` `intimate` `workplace` `aegyo`

**Deferred commit**: Actor 응답 → `PendingCommit` 저장 → 다음 턴 시작 시 `src/ui/deferred_commit.py`에서 DB 적용. reroll 시 pending 폐기, DB 무변경.

## Modules

| Path | Role |
|------|------|
| `app.py` | Chainlit entry: init/resume/routing |
| `src/agents/manager/pipeline.py` | 턴 오케스트레이션 |
| `src/agents/manager/planning.py` | scene classify + 시간 계산 |
| `src/agents/manager/core_context.py` | char/memory/event ctx 조립 |
| `src/agents/manager/world_context.py` | goal/item/secret/social ctx |
| `src/agents/manager/queries.py` | Kuzu CRUD 전체 |
| `src/agents/context/planner.py` | 이번 턴 활성화 시스템 결정 |
| `src/agents/context/renderer.py` | graph data → prompt text |
| `src/agents/prompt_factory/builder.py` | Fixed/Genre/Dynamic 조합 |
| `src/agents/prompt_factory/ooc_handler.py` | `*...*` 파싱 + 적용 |
| `src/agents/actor.py` | Actor LLM 호출 + 스트리밍 |
| `src/assets/worlds/base.py` | World 기반 클래스 + Scenario |
| `src/assets/worlds/*/schema.py` | world별 Kuzu schema + cfg |
| `src/core/database/driver.py` | KuzuAsyncDriver singleton |
| `src/core/database/helpers.py` | CRUD helpers (update_dynamic_state 등) |
| `src/core/llm/client.py` | Gemini Vertex AI wrapper |
| `src/core/embedding/encoder.py` | HF KURE-v1 (1024-dim) |
| `src/core/data_layer/json_data_layer.py` | thread/step → JSON |
| `src/simulation/state/updater.py` | 응답 후처리 총괄 |
| `src/simulation/events/manager.py` | StaticEvent: dormant→foreshadow→active |
| `src/simulation/systems/memory/` | memory 생성/decay/distort/압축 |
| `src/simulation/systems/needs/` | 6 need tracks + auto-action |
| `src/simulation/systems/personality.py` | personality micro/macro drift |
| `src/simulation/systems/reputation.py` | gossip 전파 |
| `src/ui/deferred_commit.py` | pending DB write 적용 |
| `src/ui/input_routing.py` | 메시지 → 핸들러 라우팅 |
| `src/config.py` | env var 중앙화 |

## Constraints

- **Async only**: 모든 I/O `async/await`. Kuzu는 thread pool로 래핑. blocking call 금지.
- **Fixed 불변**: Fixed prompt 내용이 턴 간 달라지면 Gemini cache miss. dynamic 내용 주입 금지.
- **Thread 격리**: thread 간 DB 쿼리 금지. world별 schema init 1회 필수.
- **New world**: `src/assets/worlds/` 하위에 `base.py` 상속 패키지 생성 + `schema.py` + `characters.py` → `python -m src.core.database.schema_builder`.
- **Memory distortion은 의도된 동작**: NPC 성격 방향으로 기억이 왜곡됨. 버그 아님.
