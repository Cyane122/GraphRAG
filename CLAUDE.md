# CLAUDE.md

Graph 기반 롤플레이 시뮬레이션 엔진. Chainlit UI + Kuzu 그래프 DB + Gemini LLM.

---

## CLAUDE.md 갱신 규칙

**파일/디렉토리 구조가 변경될 때마다 반드시 CLAUDE.md를 함께 수정해야 한다.**

| 변경 유형 | 수정 대상 |
|-----------|-----------|
| 파일 신규 생성 | [디렉토리 맵](#디렉토리-맵) 에 경로·역할 추가 |
| 파일 이동·이름 변경 | 경로 갱신 |
| 파일 삭제 | 항목 제거 |
| 패키지(폴더) 신규 생성 | 해당 섹션에 소패키지 설명 추가 |
| 아키텍처 흐름 변경 | [턴 파이프라인](#턴-파이프라인) 갱신 |
| 환경변수 추가·변경 | [Env](#env-env) 갱신 |

---

## Run

```bash
chainlit run app.py                              # 메인 앱 실행
python -m src.core.database.schema_builder --world_id <world_id>  # 월드 Kuzu 스키마 초기화
python -m src.tools.world_editor                 # world_editor FastAPI 서버 (포트 8001)
python -m src.ui.web_app                         # standalone web UI (포트 8000)
```

`cp example.env .env` → 크리덴셜 채우기. 테스트·린트 없음.

---

## Env (`.env`)

| 변수 | 용도 |
|------|------|
| `WORLD_ID` | 활성 세계관 ID (예: `babe_univ`, `rofan`, `sunghwa_high_school` …) |
| `MAX_TOKEN` | Actor 출력 상한 (기본 12288) |
| `IMPERSONATION` | `true`=PC→NPC 모드 |
| `MANAGER_PLANNER_MODE` | `legacy` / `integrated` — 컨텍스트 플래너 선택 |
| `TURN_EXTRACTOR_MODE` | `legacy` / `integrated` — 턴 추출기 선택 |
| `MODEL_ACTOR` | 롤플레이 LLM (Gemini Pro) |
| `MODEL_CLASSIFIER` | 씬/시간 분류 (Flash) |
| `MODEL_STATE_UPDATER` | 경량 상태 추출 |
| `MODEL_COMPLEX_UPDATER` | 다중 노드 갱신 (temp=0) |
| `MODEL_EVENT_CREATOR` | 이벤트 생성 + 소문 전파 |
| `MODEL_PRO_UPDATER` | 판단 기반 업데이트 |
| `MODEL_DIRECTOR` | Director beat 생성 (Pro) |
| `MODEL_MANAGER_PLANNER` | integrated 플래너용 |
| `MODEL_TURN_EXTRACTOR` | 턴 추출기용 |
| `MODEL_OUTPUT_REPAIR` | 출력 금지어 수정 (Flash) |
| `MODEL_EMBEDDER` | HF 임베딩 모델 (KURE-v1, 1024-dim) |
| `EMBEDDING_DIM` | 임베딩 차원 |
| `GOOGLE_PROJECT_ID` | Vertex AI 프로젝트 ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 JSON 경로 |
| `HF_TOKEN` | HuggingFace 토큰 |
| `OPENROUTER_API_KEY` | OpenRouter 폴백 키 |
| `CHAINLIT_AUTH_SECRET` | Chainlit 인증 시크릿 |

---

## 아키텍처

### DB: Kuzu (인-프로세스, 서버 불필요)

- 월드별 스키마를 `schema_builder`로 초기화 (1회)
- 스레드(채팅방)별 독립 DB: `data/<world_id>/<thread_id>/`
- 드라이버: `src/core/database/driver.py` (KuzuAsyncDriver + ProxyDriver)
- 스레드 메타: JSON → `data/threads/<id>.json` (`src/core/data_layer/json_data_layer.py`)

### 턴 파이프라인

```
사용자 입력
→ InputRouter    /help /debug, 빈 입력, OOC 전용, 리롤/수정/삭제 분기
→ DeferredCommit 이전 턴 pending DB write 적용 (src/ui/deferred_commit.py)
→ OOCHandler     *...* → 즉시 DB 반영 후 조기 종료 (OOC 전용 입력)
→ Manager Pipeline (src/agents/manager/pipeline.py)
    ├ world bootstrap + global state (planning.py)
    ├ scene classify + time plan     (planning.py, manager/classifier.py)
    ├ personal fact extract          (simulation/systems/personal_facts.py)
    ├ integrated/legacy context plan (integrated_planner.py, context/planner.py)
    ├ core ctx: char/memory/event/relation  (core_context.py)
    ├ dynamic ctx: goal/item/secret/social  (world_context.py)
    └ Director (병렬): beat sequence LLM    (src/agents/director.py)
→ PromptBuilder   Fixed+Genre+Dynamic 조합 (prompt_factory/builder.py)
→ Actor           스트리밍 응답 (src/agents/actor.py)
→ OutputGuard     금지어 검사 (src/ui/output_guard.py)
→ PendingStore    응답 저장 — DB write는 다음 턴으로 defer
→ [다음 턴 시작 시] StateUpdater (simulation/state/updater.py)
    ├ LITERAL/FIGURATIVE classify
    ├ multi-char state extract
    ├ event create + embed
    ├ relation/affinity/personality Δ
    ├ goal/item/secret update
    ├ time/weather/location mutate
    ├ needs decay + auto-action (resolver.py)
    ├ schedule tick
    └ memory create + decay distort + narrative 압축
```

### 3-파트 프롬프트

| 파트 | 내용 | 규칙 |
|------|------|------|
| **Fixed** | policy + world + char 고정 설명 | 턴 간 동일 → Gemini implicit cache hit |
| **Genre** | prose rules + few-shot | 씬 타입별 교체 |
| **Dynamic** | header + loc + ctx + input + hints | 매 턴 graph에서 재조립 |

씬 타입: `daily` `emotional` `physical` `intimate` `workplace` `aegyo`

**Deferred commit**: Actor 응답 → `PendingStore` 저장 → 다음 턴 시작 시 DB 적용.
reroll 시 pending 폐기, DB 무변경.

---

## 디렉토리 맵

```
GraphRAG/
├── app.py                          # Chainlit 진입점: 세션 초기화/재개/라우팅
├── src/
│   ├── config.py                   # 환경변수 중앙화 (모든 모듈이 여기서 import)
│   │
│   ├── agents/                     # LLM 에이전트 + 프롬프트 조립
│   │   ├── actor.py                # Gemini Actor 호출 + 스트리밍
│   │   ├── director.py             # Director LLM — 씬 beat sequence 생성
│   │   ├── resolver.py             # 욕구 초과 시 NPC 자율 행동 결정
│   │   │
│   │   ├── context/                # 컨텍스트 플래닝 (DB → 프롬프트 변환 전)
│   │   │   ├── generic.py          # 범용 프롬프트 컨텍스트 수집
│   │   │   ├── planner.py          # rule-based 컨텍스트 플랜 결정
│   │   │   ├── renderer.py         # graph data → 프롬프트 텍스트 렌더링
│   │   │   ├── scene_keys.py       # 씬 타입 정규화 유틸
│   │   │   ├── scene_state.py      # 씬 간 지속 상태 (transient state)
│   │   │   └── transient.py        # 위치 힌트 등 일회성 컨텍스트 정제
│   │   │
│   │   ├── manager/                # 턴 준비 파이프라인 (Manager)
│   │   │   ├── pipeline.py         # 턴 오케스트레이터 (run_manager_pipeline)
│   │   │   ├── planning.py         # world bootstrap + scene/time plan
│   │   │   ├── classifier.py       # 씬 분류 보조 로직
│   │   │   ├── core_context.py     # char/memory/event ctx 조립
│   │   │   ├── world_context.py    # goal/item/secret/social ctx 조립
│   │   │   ├── integrated_planner.py # shadow-first Pro planner (MANAGER_PLANNER_MODE=integrated)
│   │   │   ├── effects.py          # manager 부작용 DB 반영 (core + auxiliary)
│   │   │   ├── models.py           # Pydantic 데이터 모델 (ManagerBootstrap, SceneTimePlan …)
│   │   │   ├── pov.py              # POV(시점) 처리
│   │   │   ├── prompting.py        # 프롬프트 파트 결합
│   │   │   ├── queries.py          # Kuzu CRUD 전체 (fetch_character_data 등)
│   │   │   └── world_loader.py     # WORLD_ID → World 클래스 동적 로드
│   │   │
│   │   └── prompt_factory/         # 3-파트 프롬프트 조립
│   │       ├── builder.py          # PromptBuilder (Fixed/Genre/Dynamic 조합)
│   │       ├── fixed.py            # Fixed 프롬프트 생성
│   │       ├── renderers.py        # 블록 렌더링 유틸
│   │       ├── prompt_sections.py  # 섹션 단위 렌더러
│   │       ├── ooc_handler.py      # *...* OOC 파싱 + DB 반영
│   │       ├── checklist.py        # 체크리스트 프롬프트 섹션
│   │       ├── director_prompt.py  # Director system/dynamic 프롬프트 조립
│   │       ├── usernote.py         # 유저 노트 블록 생성/로드
│   │       └── prompts/            # Markdown 프롬프트 파일
│   │           ├── core/           # 핵심 정책 프롬프트
│   │           ├── blacklist/      # 금지어 목록
│   │           ├── checklist/      # 체크리스트 프롬프트
│   │           ├── emotion/        # 감정 관련 프롬프트
│   │           ├── genre_specific/ # 씬 타입별 프롬프트
│   │           ├── pov/            # 시점별 프롬프트
│   │           └── style/          # 문체 가이드
│   │
│   ├── assets/
│   │   └── worlds/                 # 세계관 정의
│   │       ├── base.py             # World 베이스 클래스 + Scenario 정의
│   │       ├── base_character.py   # NPC 베이스 클래스
│   │       ├── utils.py            # 세계관 공용 유틸
│   │       └── <world_id>/         # 세계관 패키지 (world_id별로 존재)
│   │           ├── schema.py       # Kuzu 스키마 + world config
│   │           ├── characters/     # 캐릭터 정의 파일들
│   │           └── prompt/         # 세계관 전용 프롬프트 (few_shot/, scenes/, scenarios/)
│   │
│   ├── core/                       # 인프라 레이어
│   │   ├── commit_artifacts.py     # 커밋 단위 아티팩트 저장
│   │   ├── state_normalization.py  # 상태 정규화 유틸
│   │   ├── data_layer/
│   │   │   └── json_data_layer.py  # JSON 기반 Chainlit DataLayer (threads/ 저장)
│   │   ├── database/
│   │   │   ├── driver.py           # KuzuAsyncDriver + ProxyDriver 싱글톤
│   │   │   ├── helpers.py          # CRUD helpers (update_dynamic_state 등)
│   │   │   ├── proxy.py            # 컨텍스트 기반 드라이버 프록시
│   │   │   ├── records.py          # DB 레코드 타입 정의
│   │   │   ├── schema_builder.py   # CLI: 월드 스키마 초기화
│   │   │   └── session.py          # 세션 범위 드라이버 관리
│   │   ├── embedding/
│   │   │   └── encoder.py          # HF KURE-v1 임베딩 (embed_async)
│   │   ├── llm/
│   │   │   └── client.py           # Gemini Vertex AI 래퍼 (get_model, extract_json_from_llm)
│   │   └── logging/
│   │       ├── conversation_logger.py # 턴별 대화 로그 (append_turn)
│   │       └── prompt_debug.py     # 프롬프트 fingerprint / 디버그 출력
│   │
│   ├── simulation/                 # 상태 업데이트 + 시스템 시뮬레이션
│   │   ├── events/
│   │   │   ├── manager.py          # StaticEvent 생명주기 (dormant→foreshadowing→active)
│   │   │   └── evaluator.py        # 이벤트 조건 평가기
│   │   ├── state/                  # Actor 응답 → DB 상태 변환
│   │   │   ├── updater.py          # 상태 업데이트 총괄 (process_actor_response)
│   │   │   ├── multi_character.py  # 다중 NPC 상태 추출
│   │   │   ├── turn_extractor.py   # 턴 추출기 (integrated 모드)
│   │   │   ├── dynamic_information.py # DynamicState 추출
│   │   │   ├── relationships.py    # 관계/친밀도 업데이트
│   │   │   ├── events.py           # 이벤트 생성 + 임베딩
│   │   │   ├── time_plan.py        # 시간 계획 파싱
│   │   │   ├── audit.py            # 상태 업데이트 감사 로그
│   │   │   ├── creator_slots.py    # 커스텀 슬롯 업데이트
│   │   │   └── update_policy.py    # 업데이트 정책 결정
│   │   └── systems/                # 독립 시뮬레이션 시스템
│   │       ├── memory/
│   │       │   ├── distortion.py   # 기억 왜곡 (NPC 성격 방향으로 의도된 동작)
│   │       │   └── narrative.py    # N턴마다 대화 → 타임라인 압축
│   │       ├── needs/
│   │       │   ├── store.py        # 욕구 수치 저장/조회
│   │       │   ├── math.py         # 욕구 감쇠 계산
│   │       │   ├── traits.py       # 성격 기반 욕구 초기값
│   │       │   └── location_policy.py # 위치별 욕구 정책
│   │       ├── goals/
│   │       │   └── models.py       # Goal 데이터 모델
│   │       ├── items/
│   │       │   ├── models.py       # Item 데이터 모델
│   │       │   ├── actions.py      # 아이템 상태 변경 액션
│   │       │   └── hints.py        # 아이템 힌트 렌더링
│   │       ├── secrets/
│   │       │   └── models.py       # Secret 데이터 모델
│   │       ├── social/
│   │       │   ├── context.py      # SNS 컨텍스트 수집
│   │       │   ├── graph.py        # 소셜 그래프 쿼리
│   │       │   └── promotion.py    # 소셜 상태 승격
│   │       ├── kakao/
│   │       │   └── models.py       # 카카오톡 메시지 모델
│   │       ├── organic.py          # 유기적 NPC 상태 변화
│   │       ├── personal_facts.py   # 개인 사실 추출 + 저장
│   │       ├── personality.py      # 성격 micro/macro drift
│   │       ├── reputation.py       # 소문 전파 (gossip)
│   │       ├── schedules.py        # 스케줄 컨텍스트 수집
│   │       ├── schedule_tick.py    # 스케줄 tick 처리
│   │       └── time_rules.py       # 시간 규칙 컨텍스트
│   │
│   ├── tools/
│   │   └── world_editor/           # 세계관 저작 GUI (FastAPI, 포트 8001)
│   │       ├── app.py              # FastAPI 라우트 (오케스트레이터)
│   │       ├── compiler.py         # 세계관 Python 코드 컴파일
│   │       ├── field_types.py      # 필드 타입 정의/관리
│   │       ├── migrate.py          # 스키마 마이그레이션
│   │       ├── models.py           # API 요청/응답 모델
│   │       ├── module_cache.py     # 모듈 캐시 + purge
│   │       ├── prompts.py          # 프롬프트 파일 편집
│   │       ├── repair.py           # 코드 자동 수정
│   │       ├── scaffold.py         # 신규 세계관 스캐폴딩
│   │       ├── schedules.py        # 스케줄 편집
│   │       ├── source_create.py    # 소스 파일 신규 생성
│   │       ├── source_edit.py      # 소스 파일 편집 (AST 안전 쓰기)
│   │       └── worlds.py           # 세계관 목록 / 로드
│   │
│   └── ui/                         # Chainlit UI 레이어
│       ├── actor_stream.py         # Actor 응답 스트리밍 처리
│       ├── debug_graph.py          # 디버그 그래프 표시
│       ├── deferred_commit.py      # Pending → DB 확정 (commit_pending_if_any)
│       ├── graph_loader.py         # 그래프 데이터 로드
│       ├── graph_models.py         # 그래프 UI 모델
│       ├── graph_server.py         # 그래프 서버 연동
│       ├── graph_writer.py         # 그래프 데이터 쓰기
│       ├── history.py              # 대화 history 구성
│       ├── input_routing.py        # 사용자 입력 → TurnInputType 분기
│       ├── kakao_panel.py          # 카카오톡 패널 UI
│       ├── output_guard.py         # Actor 출력 금지어 검사
│       ├── output_repair.py        # 금지어 위반 응답 수정
│       ├── pending_store.py        # PendingCommit 임시 저장소
│       ├── response_editing.py     # 응답 수정/삭제 처리
│       ├── session_models.py       # 세션 Pydantic 모델
│       ├── session_world.py        # 세션 월드 상태 관리
│       ├── social_media_settings.py # SNS 설정 UI
│       ├── status.py               # 상태 표시 UI
│       ├── time_state.py           # 인게임 시간 상태
│       ├── turn_debug.py           # 턴 디버그 출력
│       └── web_app/                # Standalone FastAPI web UI (포트 8000)
│           ├── app.py              # FastAPI 라우트
│           ├── actor.py            # Actor 호출
│           ├── analysis_tools.py   # 분석 도구
│           ├── commit.py           # 커밋 처리
│           ├── models.py           # API 모델
│           ├── runtime.py          # 런타임 상태
│           ├── service.py          # 서비스 레이어
│           ├── storage.py          # 저장소
│           └── world_state.py      # 월드 상태
│
└── scripts/                        # 개발/디버그용 스크립트 (프로덕션 무관)
```

---

## 신규 세계관 추가

1. `src/assets/worlds/<world_id>/` 패키지 생성
2. `base.py` 상속 → `schema.py` (Kuzu 스키마 + world config) + `characters/` 작성
3. `prompt/` 하위에 `few_shot/`, `scenes/`, `scenarios/` 구성
4. `python -m src.core.database.schema_builder --world_id <world_id>` 실행
5. **CLAUDE.md 디렉토리 맵에 세계관 항목 추가**

---

## 제약사항

- **Async only**: 모든 I/O `async/await`. Kuzu는 thread pool로 래핑. blocking call 금지.
- **Fixed 불변**: Fixed 프롬프트 내용이 턴 간 달라지면 Gemini cache miss. dynamic 내용 주입 금지.
- **Thread 격리**: 스레드 간 DB 쿼리 금지. `session.py`로 드라이버 범위 관리.
- **기억 왜곡은 의도된 동작**: NPC 성격 방향으로 기억이 왜곡됨. 버그 아님.
- **config.py 경유**: 환경변수는 `src/config.py`에서만 읽는다. 다른 파일에서 `os.getenv` 직접 호출 금지.
