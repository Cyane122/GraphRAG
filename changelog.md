## 2026-04-19
- GraphRAG 아이디어 브레인스토밍
- 라이브러리 설치했음
- 가상환경도 만들었음
- 그래프 스키마 빌더 (graph.schemaBuilder.py) 만들었음
- 실제로 작동할 Agent 2종 (agents.actor_agent / agents.manager_agent) 만들었음
  - actor_agent: 실제 롤플레이 메인 챗봇
  - manager_agent: 롤플레이 메인 챗봇의 출력으로 인해 변화하는 내용 그래프에 반영
- Neo4j 연결 성공 (test_connection.py)

## 2026-04-20
- Claude Credit $20 결제 완.
- app.py
  - config.toml 수정
  - stylesheet.css 수정
  - `chainlit run app.py`를 통한 실행 구현
- .env
  - 기본 모델 `claude-haiku-4-5-20251001`로 수정
  - Actor 모델:
    - 개발 중: `claude-haiku-4-5-20251001`
    - 튜닝 및 개발 완료: `claude-sonnet-4-6`
    - 목표: `claude-opus-4-7`
  - model classifier: `meta-llama/llama-3.3-70b-instruct:free` / `google/gemma-3-12b-it:free`
- schemaBuilder.py
  - 추가 등장인물 7인
  - HP 제거. 동적 현재 상태 구현 완료
- agents, ooc, updater: 프롬프트 영어로 번역. 프롬프트 추가 튜닝.

## 2026-04-21
- 출력 잘림 현상 완화
- 메인 프롬프트 `promptBuilder.py` 동적 모듈화 완료.
  - `schemaBuilder.py`는 더 이상 바베대학교 세계관 스키마를 만들지 않음
  - `graph.world.*`에서 세계관 설정 프롬프트, 세계관 스키마를 제작함
  - `promptBuilder.py`에서 모든 'Eun-seo', 'Sian' 텍스트를 {char}, {user}로 변환함
- `default.py`: `build_schema()` 함수는 이제 GlobalState 노드를 생성함
  - `build_schema()` 함수를 상속받아 현재 시작 위치와 날씨를 지정할 수 있음
- `time_manager.py`: 이제 AI가 시간을 계산하여 GlobalState 노드에 저장함
- `ooc_parser`: 더 이상 현재 시간과 장소를 계산하지 않음
- `promptBuilder.py`, `babe_univ.py`: 이제 세계관과 범용 프롬프트를 완전히 구분함.
- 이제 `manager_agent.py`가 `run_manager.py`에서 `world_id`를 변수로 사용함.

## 2026-04-22
- `complex_updater.py`, `expression_classifier.py`, `ooc_parser.py`, `state_updater.py`, `time_manager.py` 리팩토링 및 중복된 기능 삭제
- `actor_agent.py`, `manager_agent.py` 리팩토링
- `promptBuilder.py`, `app.py`: 리팩토링 대응 수정
- `utils/db_utils.py`, `utils.llm_utils.py` 추가

## 2026-04-23
- 모든 `datetime.now()`를 별도 세계관별 `start_time` 변수로 전환함
  - `app.py`의 로깅용 변수 하나는 제외함
- `complex_updater.py`, `manager_agent.py`, `time_manager.py`, `app.py`: `async` 관련 비동기 문제점 수정 및 하드코딩 제거
- `manager_agent.py`: 오타 수정
- `app.py`: `response_msg` 이중 호출 문제 수정. 이제 `run_manager.py`의 지연 커밋 로직과 중복되지 않으며, 하드코딩 제거됨
- `llm_utils.py`: `extract_json_from_llm`은 이제 JSON 배열도 파싱할 수 있음
- `promptBuilder.py`: HOT-COLD Channel 프롬프트 추가

## 2026-04-24
- `promptBuilder.py`: 프롬프트 압축 및 개선. 중복된 내용 제거.
- `app.py`: `await` 동기화 오류 수정. 불러오기 기능 추가.
- `example.env`: `.env` 예시 파일 추가
- `embedder.py`: 임베딩 모델 추가
- `conversation_logger.py`: 채팅 저장 및 불러오기 기능 추가.
- [신규] 욕구 시스템 추가
  - `needs_manager.py`
  - `traits_initializer.py`
  - `action_resolver.py`
- [신규] 스키마 대응 추가
  - `babe_univ.py`: `sexual_tendency`, `libido_drive_modifer`, `libido_excluded` 추가. `trait_*` 필드 추가
- `manager_agent.py`, `time_manager.py`, `state_updater.py`, `app.py`: LLM 호출 최적화.
- `app.py`: 코드 간결화 및 리팩토링. 이제 `calculate_and_update_time` 함수를 사용하지 않음. (`manager_agent.py`로 이관)

## 2026-04-25
- [신규] 신규 캐릭터 추가 기능
  - `world/world_builder.py`: 이제 챗봇이 새로운 캐릭터의 노드를 생성함
- `embedder.py`: 원하는 임베딩 모델 사용 가능하도록 개선
- LLM 호출 최적화: `state_updater.py`, `complex_updater.py`
- [신규] 기억 왜곡 기능 추가
  - `decay_manager.py`: 이제 중요하지 않은 기억은 점차 NPC에게 유리하게 변질되어 기억됨
  - `promptBuilder.py`: 이제 왜곡된 기억이라는 사실을 LLM에게 전달함
- `promptBuilder.py`, `babe_univ.py`: 긍정형 프롬프트로 수정.
- `app.py`: 재구조화. 이제 더 깔끔하게 출력됨.
- [신규] 대화 리롤 기능 추가
- `db_utils.py`: 중복 함수 삭제.
- `action_resolver.py`, `complex_updater.py`: Event 노드 제거. 이제 Memory만으로 기억함.
- [신규] 그래도 세상은 움직인다 - SNS 기능 추가
- `llm_utils.py`: async client 추가 및 일부 client 전환

## 2026-04-26
- [신규] 테마 설정 완료
- `promptBuilder.py`, `babe_univ.py`: 프롬프트 개선
- `count_tokens.py`, `test_connection.py`: 이제 `GraphRAG\script`에 위치함
- `llm_utils.py`: `extract_json_from_llm`이 haiku의 불안정한 응답에도 강건하도록 수정함
- `app.py`: 대화 로그 저장 기능 추가

## 2026-04-28
- [신규] 1인칭-3인칭 분리 기능 추가
- [신규] 연령대 설정 기능 추가 (r18, 15, all)

## 2026-05-03
- [변경] LLM 모델 Gemini 3.1 Pro, Gemini 3.0 Flash로 변경
- [신규] Hugging Face Token 추가
- [신규] `pregenancy_manager.py` 추가.

## 2026-05-05
- [리팩토링] 전면 리팩토링.
- [변경] Graph 아키텍처 `neo4j` -> `kuzu`로 변경.
- [변경] Graph DB 저장 경로 변경: `data/{world_id}.kuzu` → `graph/{world_id}`
  - `src/core/database/driver.py`, `src/core/database/schema_builder.py` 수정
- [신규] `src/core/database/helpers.py`: `load_graph_info()` 함수 추가
  - `.env`의 `WORLD_ID`를 자동으로 읽어 해당 그래프의 전역 상태·캐릭터·장소·관계를 dict로 반환
- [정리] `.gitignore`에 명시된 파일들을 git tracking에서 제거
  - `.chainlit/`, `.claude/`, `logs/`, `graph/sses.backup`, `project-*.json` (총 36개)

## 2026-05-06
- `src/assets/worlds/base.py`
  - 이제 `Memory` 노드 스키마가 실제 코드와 일치함
  - `REMEMBERS` 노드 스키마가 실제 코드와 일치하도록 변경
  - 통일되어 있지 않았던 벡터 인덱스 이름이 통일되도록 변경
  - `Event` 노드의 필드명을 `timestamp`로 변경.
- `src/simulation/systems/memory.py`
  - [추가] `ensure_memories_for_event()`: Memory 노드 생성 후 `OF_EVENT` 엣지 추가.
- `src/simulation/state/updater.py`
  - `_create_event()`: 이제 `embedding` 필드도 추가함.
  - 스키마에 필요없는 필드 3개 제거.
- `src/core/database/schema_builder.py`
  - 이제 DB 삭제 로직을 파일과 폴더 양쪽에서 처리함
- `src/assets/worlds/.../schema.py`
  - Location 노드 CREATE 쿼리에서 `$desc` 파라미터가 Kuzu 파서 오류를 일으키던 문제 수정
- `src/agents/manager.py`
  - `fetch_recent_events()`: 이제 `pc_id`도 받아 NPC·PC 양쪽의 Memory를 함께 조회함
  - `builder.build()` 호출부: `events`에 `npc_memory`, `pc_memory` 포함한 전체 dict 전달
- `src/agents/prompt_factory/builder.py`
  - `build_events_section()`: `@staticmethod` → 인스턴스 메서드로 전환
  - 이제 각 Event 아래에 NPC·PC의 주관적 기억(`Memory.summary`)을 함께 렌더링함
  - 출력 형식: `└ {캐릭터명}의 기억: {memory_summary}` (Memory가 없으면 생략)
  - [신규] `_DEFAULT_STATE_FIELDS`, `_render_state_line()`: DynamicState 핵심 필드를 `<analyze>` 체크리스트의 `STATE:` 라인으로 렌더링하는 레지스트리 기반 시스템 추가
    - 기본 필드: `mood`, `physical_condition`, `mental_condition`, `stress_level`, `outfit`, `injury_marks`
    - `injury_marks`가 `"없음"`이거나 `outfit`이 미설정이면 자동 생략
    - `world_config["extra_state_fields"]`에 `(key, label, frozenset(skip_values))` 목록을 추가하면 세계별 커스텀 필드가 STATE 라인에 자동 포함됨 (코드 수정 불필요)
  - `_CHECKLIST_3P`, `_CHECKLIST_1P`: `STATE: {state_line}` 라인 추가. LLM이 씬 생성 전 현재 의상·컨디션·부상 상태를 명시적으로 인식하도록 강제함
- [정리] `scripts/`를 git tracking에서 제거 (로컬 파일은 유지, `.gitignore` 이미 설정되어 있었음)
- [리팩토링] 환경변수 참조 `src/config.py`로 중앙화
  - [신규] `src/config.py`: 모든 `.env` 환경변수를 한 곳에서 읽어 타입 변환 후 상수로 제공
  - 19개 파일에서 `os.getenv` / `load_dotenv` 직접 호출 제거, `from src.config import ...`로 교체
    - `src/core/database/driver.py`, `src/core/database/schema_builder.py`
    - `src/core/llm/client.py`, `src/core/embedding/encoder.py`
    - `src/agents/actor.py`, `src/agents/manager.py`, `src/agents/resolver.py`
    - `src/agents/prompt_factory/builder.py`, `src/agents/prompt_factory/ooc_handler.py`
    - `src/simulation/state/updater.py`, `src/simulation/state/classifier.py`
    - `src/simulation/systems/memory.py`, `src/simulation/systems/needs.py`, `src/simulation/systems/social.py`
    - `src/assets/worlds/babe_univ_altered/schema.py`, `src/assets/worlds/rofan/schema.py`, `src/assets/worlds/sses/schema.py`
    - `app.py`
  - `MODEL_STATE_UPDATER` 기본값 파일 간 불일치 해소 (→ `gemini-3-flash-preview`로 통일)
- [신규] `src/simulation/events/` 패키지 추가 — 조건 기반 이벤트(StaticEvent) 시스템
  - `evaluator.py`: `time` / `stat` / `flag` 세 가지 조건 타입 평가 (time은 MM-DD 정수 비교, stat은 RELATIONSHIP 쿼리, flag는 GlobalState.flags JSON 조회)
  - `manager.py`: `evaluate_all()` — 매 턴 모든 StaticEvent 상태를 갱신하고 foreshadowing/active 힌트 목록 반환. `set_flag()` — Complex Updater 등에서 서사적 조건 충족 시 호출
  - `__init__.py`: `evaluate_all`, `set_flag` 노출
  - StaticEvent 상태: `dormant` → `foreshadowing` → `active` → `done`
  - foreshadow_conditions / trigger_conditions 두 단계 분리로 복선과 발화를 구분함
- [변경] `src/assets/worlds/base.py`
  - `StaticEvent` 노드 테이블 추가 (`foreshadow_conditions`, `foreshadow_hint`, `trigger_conditions`, `status`)
  - `EVENT_INVOLVES` 관계 테이블 추가 (StaticEvent → Character)
  - `GlobalState`에 `flags STRING` 컬럼 추가 (flag 타입 조건 저장용)
- [변경] `src/agents/prompt_factory/builder.py`
  - `build_world_section()`: `static_events` 키 지원 추가. foreshadowing 이벤트는 `[예정]`, active는 `[오늘]` 레이블로 `<world_context>` 블록에 렌더링
- [변경] `src/agents/manager.py`
  - step 7.6 추가: 매 턴 `evaluate_static_events()` 호출 → 활성 힌트를 `world_context["static_events"]`에 주입
- [최적화] LLM 호출 횟수 축소
  - `src/simulation/state/updater.py`: Classifier + Complex Updater + Relationship Status 재작성을 `_run_combined_update()` 단일 호출로 통합
    - 복합 턴 기준 최대 3회 → 1회로 축소
    - `_evolve_relationship_status()` 제거 — LLM 출력 JSON의 `new_event.new_relationship_status` 필드로 통합
    - `delegate_complex_update()`: event_only 경로 전용으로 단순화. `_generate_event_plan()` 사용
    - `process_actor_response()` 반환형 `dict` → `str | None` (임신 OOC 메시지 직접 반환, 기존 버그 수정)
  - `src/simulation/systems/memory.py`: 기억 왜곡·압축을 개별 호출 → 배치 호출로 전환
    - `_distort_memory()`, `_compress_memory()` 제거
    - `_distort_memories_batch()`: 동일 캐릭터의 왜곡 대상 전체를 JSON 배열 1회 호출로 처리
    - `_compress_memories_batch()`: 압축 대상 전체를 레벨별 1회 호출로 처리
    - `run_decay()`: 버킷 분류(삭제/압축L2/압축L1/왜곡) 후 버킷당 1회 배치 처리로 재구조화
- [신규] **관계에 깊이 더하기** (TODO 4)
  - `src/simulation/systems/reputation.py` 신규: 사회적 평판과 소문 전파 시스템
    - 중요도 ≥ 5 + |affinity delta| ≥ 3인 이벤트 발생 시 source NPC 지인에게 소문 확산
    - 배치 LLM 호출로 NPC별 소문 내용·호감도 변화 생성 (원본 delta의 35% 강도)
    - 수신 NPC에게 gossip Memory 노드 생성 → 이후 프롬프트 벡터 검색에 반영됨
  - `src/simulation/systems/memory.py` 강화: 호감도 급변 즉시 기억 왜곡
    - `distort_on_affinity_change()` 추가: |delta| ≥ 10 시 공유 기억 최대 3개 즉시 재해석
    - 부정 방향 → "미처 못 봤던 경고 신호 회상 / 평범한 순간에 불안감"
    - 긍정 방향 → "따뜻한 면 부각 / 애매한 순간 호의적으로 재해석"
    - `_build_trait_hints()` / `_distort_memories_with_hints()` 분리로 힌트 로직 재사용 가능
  - `src/simulation/systems/personality.py` 신규: 성격 변화 시스템
    - micro-drift: affinity ≥ 65 + delta > 0 + 30일 쿨다운 → 성격 1~2가지 미세 조정
    - macro-drift: importance ≥ 9 이벤트 → Personality.props 전면 재작성
    - `last_drifted_at` / `macro_drift_count`를 props JSON에 내장해 상태 추적
  - `src/simulation/state/updater.py`: 관계 깊이 파이프라인 블록 추가
    - `process_actor_response()` 내 소셜 리졸버 직후, 임신 체크 직전에 3개 기능 순차 실행
    - game time DB 조회 1회로 공유 후 각 기능에 전달 (불필요한 중복 조회 방지)