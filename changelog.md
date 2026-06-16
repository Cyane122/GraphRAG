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
- [신규] **관계에 깊이 더하기**
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

## 2026-05-07
- [리팩토링] `narrator / pc / chars` 패턴 전 세계관으로 확장
  - `src/assets/worlds/.../schema.py`: 15개 캐릭터 클래스 임포트 후 `world_instance` 생성 인자로 전달
  - `src/assets/worlds/default/schema.py`: `Char`, `Player` 임포트 후 `world_instance` 생성 인자로 전달
  - `get_pc_id()`, `get_npc_id()`, `npc_name_kor()` 오버라이드 제거 (base 위임)
- [버그픽스] Kuzu 호환성 오류 다수 수정
  - `src/core/database/driver.py`
    - `KuzuRecord`에 `keys()` 메서드 추가 — `dict(row)` 호출 시 정수 인덱스로 접근하던 KeyError 수정
  - `src/core/database/helpers.py`
    - `move_location()`: Kuzu 미지원 list comprehension `[x IN list WHERE cond]` → `list_filter(list, x -> cond)` 로 교체
    - DELETE + SET 혼합 쿼리를 두 개의 독립 쿼리로 분리 (SET 먼저, DELETE 이후)
  - `src/simulation/systems/needs.py`
    - `_fetch_all_npcs()`: `sp.libido_excluded` 프로퍼티가 스키마에 없어 발생하던 Binder 오류 제거 — OPTIONAL MATCH 블록 전체 삭제
    - `_load_profile()`, `_fetch_needs()`, `_fetch_profile_props()`: `RETURN properties(x) AS props` → `RETURN x AS props` 로 교체 (4개소)
  - `src/agents/manager.py`: `RETURN properties(x) AS props` → `RETURN x AS props` 로 교체 (5개소)
  - `src/simulation/systems/memory.py`: `RETURN properties(n) AS props` → `RETURN n AS props` 로 교체 (1개소)
  - `src/simulation/systems/social.py`: `RETURN properties(sp) AS props` → `RETURN sp AS props` 로 교체 (1개소)
  - **근본 원인**: Kuzu의 `properties()` 함수는 Neo4j와 달리 `(LIST, STRING) -> ANY` 시그니처를 가지며 노드 전체 속성 맵을 반환하지 않음. 노드를 직접 `RETURN`하면 모든 프로퍼티가 dict로 반환됨.

## 2026-05-08
- [버그픽스] 2026-05-07 Kuzu 호환성 수정 후 남아 있던 동일 계열 오류 정리
  - `src/simulation/systems/needs.py`
    - `_fetch_profile_props()`: `StaticProfile` 노드 전체가 아니라 `sp.props` JSON blob을 직접 조회하고 파싱하도록 변경
    - `_fetch_needs()`: Kuzu에서 파싱되지 않는 `CREATE (n:NeedsState $props)` 문법을 명시적 필드 생성 쿼리로 교체
    - `_calc_multiplier()`: `mental_condition` 문자열을 `int()`로 변환하던 오류 제거
  - `src/simulation/state/updater.py`
    - `_update_acceptance_scores()`: Kuzu에서 실패하던 `max()/min()` 중첩 클램프를 `CASE WHEN` 기반 클램프로 교체
  - `src/agents/manager.py`
    - Memory recall 벡터 검색을 Neo4j 문법 `db.index.vector.queryNodes()`에서 Kuzu 문법 `QUERY_VECTOR_INDEX()`로 교체
    - Kuzu의 `distance` 값을 기존 prompt score 의미에 맞게 `1 - distance` 형태로 변환
- [신규] TODO-4 **삶은 넓어지며 또한 깊어진다** 2차 구현
  - `src/simulation/systems/goals.py` 신규: NPC 장기 목표 시스템
    - `fetch_goal_hints()`: active `Goal`을 Dynamic prompt용 은근한 행동/일정 압박 힌트로 반환
    - `apply_goal_updates()`: Actor 응답 확정 후 목표 진행도, 상태, 다음 힌트 갱신
    - `PURSUES`, `GOAL_RELATED_EVENT` 관계로 캐릭터·이벤트와 연결
  - `src/simulation/systems/items.py` 신규: 물건에 담긴 추억 시스템
    - `fetch_object_memory_hints()`: 현재 위치/소유자/유저 입력/벡터 recall 기반으로 Item-anchored Memory 힌트 조회
    - `apply_item_updates()`: 기존 Item의 위치·소유자·분실 상태·설명·앵커 기억을 보수적으로 갱신
    - `ensure_item_memory()`: `Item` → `ANCHORS_MEMORY` → `Memory`, `Character` → `REMEMBERS` 연결 생성
  - `src/simulation/systems/secrets.py` 신규: 조건부 비밀/서브텍스트 시스템
    - `fetch_secret_hints()`: 조건을 만족한 `Secret`의 `public_hint`만 prompt에 제공하고 `private_summary`는 노출하지 않음
    - `apply_secret_updates()`: reveal 조건 충족 시 reveal level/status 갱신 및 이벤트 연결
  - `src/assets/worlds/base.py`
    - `Goal`, `Secret`, 확장 `Item` 노드 테이블 추가
    - `PURSUES`, `GOAL_RELATED_EVENT`, `OWNS`, `GAVE`, `ANCHORS_MEMORY`, `ROOTED_IN`, `TRIGGERED_BY` 관계 테이블 추가
  - `src/agents/manager.py`
    - step 7.7 추가: goal / object memory / secret hint를 `world_context`에 동적 주입
  - `src/agents/prompt_factory/builder.py`
    - `<world_context>`에 `[Life Goals]`, `[Object Memories]`, `[Subtext]` 블록 렌더링
    - Fixed prompt가 아니라 Dynamic prompt에만 넣어 Gemini implicit cache 안정성 유지
  - `src/simulation/state/updater.py`
    - Actor 응답 확정 후 `apply_goal_updates()`, `apply_item_updates()`, `apply_secret_updates()` 후처리 호출
- [신규] TODO 문서 전면 재작성
  - `TODO.md`: 단순 TODO-4 목록에서 엔진 안정화 로드맵 문서로 확장
  - 1차 작업: Turn Router, Deferred Commit / State Diff, State Update Guard 정리
  - 2차 작업: Manager 책임 분리, SceneState / Context Planner, Dynamic Context Budgeter / Renderer 정리
  - 3차 작업: 범용 노드, 장기 시뮬레이션, 확장 보류 항목 정리
- [신규] Turn Router 1차 구현
  - `app.py`
    - `TurnInputType` enum 추가: `roleplay`, `ooc_patch`, `lore_qa`, `reroll`, `edit`, `system_command`, `empty`
    - `route_user_input(user_input, message)` 추가. 라우터는 DB write나 LLM 호출 없이 입력 경로만 판별
    - `on_message`를 입력 유형별 early return 구조로 정리
    - OOC-only 입력은 `parse_ooc` 처리 후 Actor 생성 없이 종료
    - OOC + RP가 섞인 입력은 OOC 처리 후 기존 RP 생성으로 진행
    - 설정/관계/상태성 질문은 Actor RP 대신 시스템 QA 응답으로 분리
    - `/reroll`, `/help`, `/status` 시스템 명령 경로 추가
    - 리롤 버튼 콜백 로직을 `_reroll_pending_response()`로 분리해 명령 경로와 공유
- [신규] Chainlit UI 상태 토스트 추가
  - `public/elements/StatusToast.jsx` 신규: 중앙 오버레이 형태의 상태 메시지 컴포넌트
  - `app.py`
    - `_send_status_toast()` 추가
    - Actor 생성 중 / deferred commit 중 상태 메시지를 일반 채팅 메시지 대신 토스트로 출력
    - 응답 헤더에서 시각을 못 읽으면 DB 인게임 시간으로 TimeTheme fallback 적용
  - `public/stylesheet.css`
    - 시간대 배경과 말풍선 스타일 재정리
    - `.status-toast-overlay`, `.status-toast-box` 스타일 추가
    - Chainlit 기본 메시지 카드 중첩/배경을 줄여 토스트와 본문이 겹치지 않도록 조정
  - `public/elements/EditableMessage.jsx`
    - Chainlit props 중첩 구조 대응 추가
    - `useEffect`로 편집 대상 응답 변경 시 textarea 내용 동기화
- [버그픽스] Kuzu 스키마/마이그레이션 및 NeedsState 안정화
  - `src/assets/worlds/base.py`
    - `DynamicState` 확장 필드 추가 (`outfit`, `injury_marks`, 임신/수용도/외형/심리 보조 필드 등)
    - `Location.district`, `GlobalState.today_schedule`, `GlobalState.schedule_date` 추가
    - `StaticProfile`에 `age`, `gender`, `role` 컬럼 추가
  - `src/core/database/driver.py`
    - 시작 시 누락된 `NeedsState`, `HAS_NEEDS` 테이블을 생성하는 migration 추가
    - Kuzu ALTER 문법에 맞춰 컬럼 migration을 `ADD` 기반으로 정리
    - migration 실패 사유를 구분해 이미 존재하는 컬럼/테이블은 조용히 skip
  - `src/core/database/helpers.py`
    - `DYNAMIC_STATE_FIELDS` whitelist 추가로 잘못된 DynamicState 필드 write 방지
    - `update_relationship_affinity()`가 `null` affinity에서도 안전하게 clamp되도록 `coalesce` 적용
  - `src/core/database/__init__.py`
    - schema builder 등에서 패키지 임포트만 해도 active Kuzu store가 열리지 않도록 lazy export로 변경
- [버그픽스] Needs / Resolver / Social 계열 null 안정화
  - `src/simulation/systems/needs.py`
    - 욕구 수치를 DynamicState가 아니라 `NeedsState`에 저장하도록 정리
    - `NeedsState`가 없으면 기본값으로 생성 후 갱신
    - `_as_float()`, `_as_int()` 추가로 DB null 및 LLM 비정상 값을 안전하게 처리
    - trait 생성 실패 시 0값 trait을 DB에 저장하지 않고 기본값만 사용
    - 저장된 trait이 모두 0이면 미완성으로 보고 재생성 가능하게 변경
  - `src/agents/resolver.py`
    - 욕구 해소 후 `NeedsState`를 생성/갱신하도록 `_settle_need()` 재작성
    - `importance`, duration 등 nullable LLM 값 처리 안정화
  - `src/simulation/systems/social.py`, `src/simulation/systems/personality.py`
    - `initial_affinity`, `appearance_count`, `macro_drift_count`가 null일 때 기본값 사용
- [버그픽스] 이벤트/상태 업데이트 가드 강화
  - `src/simulation/events/evaluator.py`
    - StaticEvent stat 조건에서 허용된 관계 필드(`affinity`, `trust`)만 평가하도록 제한
  - `src/simulation/events/manager.py`
    - `foreshadow_hint` alias 불일치 수정
  - `src/simulation/state/classifier.py`
    - `affinity`를 DynamicState safe field에서 제거해 호감도 변경이 잘못된 경로로 들어가지 않도록 수정
  - `src/core/llm/client.py`
    - JSON 파싱 실패 로그가 너무 길어지지 않도록 raw preview truncation 추가
- [문서] `readme.md`
  - `goals.py`, `items.py`, `secrets.py` 모듈 설명 추가
  - 파이프라인 설명에 Life Goals / Object Memories / Secret/Subtext 수집 및 후처리 단계 추가
  - Dynamic prompt의 life-depth hints와 Fixed prompt cache 분리 원칙 문서화
- [리팩토링] Deferred Commit / State Diff 1차 안정화
  - `src/simulation/state/updater.py`
    - `build_time_plan()`: 시간/날씨/위치 변경을 DB write 없이 계산
    - `commit_time_plan()`: 계산된 시간 계획을 확정 시점에만 DB에 반영
    - 기존 `apply_time_updates()`는 위 두 단계를 사용하는 호환 wrapper로 유지
  - `src/agents/manager.py`
    - `run_manager()`에서 시간/위치 DB write 제거
    - Actor 전 needs update, memory decay, cycle tick 실행 제거
    - 예상 시각/장소는 prompt context 계산에만 사용
    - `manager_effects`, `time_plan`, `pending_effects` 구조를 반환하도록 `return_meta` 옵션 추가
    - `commit_manager_effects()` 추가: pending 확정 시 time / needs / decay / cycle / StaticEvent side effect를 순차 commit
    - StaticEvent 평가는 Actor 전에는 `commit=False`로 hint만 계산하고, 확정 시점에 상태를 갱신
  - `app.py`
    - `_run_generation()`이 `manager_effects`, `time_plan`, `pending_effects`, `pending_state_diff`를 `pending_commit`에 저장
    - `_commit_pending()`과 `on_chat_end()`가 Actor 응답 후처리 전에 `commit_manager_effects()`를 실행
- [신규] 최종 프롬프트 디버그 로그 저장
  - `app.py`
    - `_write_turn_debug_snapshot()` 추가
    - Actor 호출 직전 `logs/turn_debug/<timestamp>/`에 `fixed_prompt.txt`, `genre_prompt.txt`, `dynamic_prompt.txt`, `final_prompt.txt`, `history.json`, `metadata.json`, `summary.md` 저장
    - `metadata.json`에 scene types, user input, manager effects, time plan, pending effects, prompt 길이 기록
    - `pending_commit["debug_dir"]`에 해당 디버그 폴더 경로 저장
- [버그픽스] Dynamic prompt `<state>` null 필드 정리
  - `src/agents/prompt_factory/builder.py`
    - `_clean_prompt_dict()` 추가
    - Kuzu가 노드 반환 시 포함하는 `_id`, `_label`, null 컬럼을 prompt JSON 렌더링 전에 제거
    - `<static>`, `<personality>`, `<state>`, `<intimate>`, `<workplace>` 블록에 공통 적용
    - 기존 `final_prompt.txt`에서 `<state>`에 `Location`, `GlobalState`, `Event`, `Memory`, `Goal`, `Secret` 계열 null 필드가 섞이던 문제 수정
- [신규] State Update Guard / Confidence 1차 구현
  - `src/simulation/state/updater.py`
    - `guard_actor_response()` 추가: Actor 응답이 State Updater로 넘어가기 전 시스템 프롬프트 누출, private secret 과노출, PC 조작, 비유적 신체 표현 위험을 rule-based로 검사
    - guard reject 시 DynamicState / Relationship / Event / Memory / LifeDepth 후처리를 실행하지 않고 종료
    - `_audit_state_updates()`: DynamicState 후보마다 `confidence`, `evidence`, `commit_policy` 생성
    - 물리 상태 필드는 실제 부상/질병 evidence가 없거나 비유 표현만 있으면 `hold`/`reject`
    - `_audit_relationship_delta()`, `_audit_event_candidate()` 추가로 관계 delta와 Event 후보도 confidence/evidence 기반으로 commit 여부 결정
    - `logs/state_audit/<timestamp>.json`에 guard 결과, state 후보, relationship 후보, event 후보 저장

## 2026-05-09
- [버그픽스] Actor 히스토리 컨텍스트 과다 주입 수정
  - `app.py`
    - `conversation_history`에 사용자 메시지로 `dynamic_prompt` 전체를 저장하던 문제 수정
    - 이제 history에는 실제 `user_input`만 저장하고, 현재 턴의 graph/dynamic context는 별도 `dynamic_prompt`로만 전달
    - 최근 10턴 히스토리 제한은 유지하되, 이전 턴마다 `<character>`, `<world_context>`, dialogue examples가 중복 주입되는 문제 제거
- [버그픽스] Relationship context 방향 및 렌더링 수정
  - `src/agents/manager_pipeline.py`
    - 관계 조회 방향을 실제 DB 구조에 맞춰 `npc_id -> pc_id`로 수정
    - social context 생성 시 generic prompt context를 덮어쓰지 않고 병합하도록 수정
    - recall memory에 `memory_type`을 유지해 renderer까지 전달
  - `src/agents/context_renderer.py`
    - relationship renderer가 `current_status`, `type`, `trust`, `last_interaction`을 출력하도록 수정
    - secondary NPC 관계 힌트도 `current_status`를 우선 사용할 수 있도록 수정
- [버그픽스] 채팅 종료 시 마지막 pending 후처리 유실 가능성 완화
  - `app.py`
    - `on_chat_end()`에서 `process_actor_response()`를 `asyncio.create_task()`로 fire-and-forget 처리하던 부분을 `await`로 변경
    - 마지막 응답 직후 세션 종료 시 state update가 누락될 가능성 감소

## 2026-05-10
- [신규] ContextRenderer numeric state band 렌더링 추가
  - `src/agents/context_renderer.py`
    - `DynamicState`의 `mood`, `stress_level`, `ts_acceptance`, `needs` 계열 수치를 Actor용 band 힌트로 렌더링
    - `RELATIONSHIP.affinity`, `RELATIONSHIP.trust`를 `distant/cautious/comfortable/intimate/deeply bonded`, `guarded/uncertain/trusting/secure/unwavering` 계열 band로 해석
    - `RelationshipProfile` 렌더링에 현재 affinity/trust band를 함께 붙여, 원본 `prompt_hint`를 수정하지 않고 이번 턴 표현 강도만 조절
    - 0~1 스케일의 needs와 0~10 스케일의 stress를 각각 별도 정규화해 band 오해를 방지
  - `src/agents/manager_prompting.py`
    - `char_data.dynamic_state`를 `build_rendered_dynamic_context()`에 전달해 dynamic context 단계에서 수치 band를 만들 수 있게 변경
  - `src/agents/prompt_factory/renderers.py`
    - pre-rendered `<world_context>` 조립 순서에 `state` 블록 추가
  - `TODO.md`
    - numeric state band renderer, RelationshipProfile affinity/trust band, DynamicState stress/mood/needs band 항목 완료 처리
- [리팩토링] 비대 파일 분리 및 패키지 경계 정리
  - `src/agents/prompt_factory/builder.py`
    - 프롬프트 상수와 렌더링 세부 구현을 builder 밖으로 분리
    - `PromptBuilder`는 Fixed / Genre / Dynamic 프롬프트 조립 흐름 중심으로 축소
    - `prompt_sections.py`, `fixed.py`, `checklist.py`, `renderers.py` 추가
  - `src/agents/manager_pipeline.py`
    - 파이프라인 파일을 orchestration 전용으로 축소
    - `manager_models.py`, `manager_planning.py`, `manager_core_context.py`, `manager_prompting.py`, `manager_world_context.py` 추가
  - `src/agents/manager.py`
    - scene classifier, graph query, world loader, effect commit 책임을 전용 모듈로 분리
    - `manager_classifier.py`, `manager_queries.py`, `manager_world_loader.py`, `manager_effects.py` 추가
  - `app.py`
    - Chainlit helper 로직을 `src/ui/`로 분리
    - `input_routing.py`, `turn_debug.py`, `actor_stream.py`, `time_state.py`, `response_editing.py`, `status.py`, `deferred_commit.py` 추가
  - `src/simulation/state/updater.py`
    - audit, time planning, event update 책임을 `audit.py`, `time_plan.py`, `events.py`로 분리
  - `src/simulation/systems`
    - flat module 구조를 `items/`, `memory/`, `needs/`, `social/`, `goals/`, `secrets/` 하위 패키지로 재구성
    - 기존 public import 경로는 각 패키지 `__init__.py`에서 유지
  - [정리] 깨진 파일 헤더 주석 정리 및 UTF-8 확인
    - Python 파일 121개 UTF-8 디코딩 확인
    - mojibake가 남은 분리 파일 27개의 헤더 주석 교체
    - 함수 이동 후 stale private symbol reference 검색 완료
  - Priority 0 안정화:
    - OOC 시간 패치 결과에 `time_before`, `time_after`, `applied_time_delta_minutes`, `applied_time_set` 반환 추가.
    - OOC+RP 혼합 입력에서 OOC로 이미 적용된 시간을 Manager time plan이 다시 증가시키지 않도록 no-op planning 처리.
    - Chainlit OOC step과 turn debug metadata에서 OOC 시간 변경 내용을 확인할 수 있게 정리.
    - 신규 schema에서 `HAS_DYNAMIC_STATE` 생성 중단, 기존 legacy 관계는 시작 migration에서 `HAS_STATE`로 backfill.
    - `AGENTS.md` schema 초기화 명령과 주요 모듈 경로를 현재 Kuzu 기반 구조로 동기화.

## 2026-05-12
- [신규] **prompt_factory 프롬프트 마크다운 파일 외부화**
  - `src/agents/prompt_factory/builder.py`
    - 빌더 내 인라인 상수(BLACKLIST, EMOTION_ENGINE, CHECKLIST, 운영자 정책 등)를 모두 외부 `.md` 파일로 분리
    - 빌더는 조립 흐름만 담당하고 실제 텍스트는 파일에서 읽도록 변경
  - 신규 프롬프트 파일 목록
    - `prompts/blacklist/BLACKLIST.md` — 금지어·금지 패턴 섹션
    - `prompts/checklist/CHECKLIST_{1P,3P}_{CHAR,USER}_NARRATOR.md` — POV별 응답 체크리스트
    - `prompts/core/CORE_{1P,3P}.md` — Fixed 프롬프트 핵심 지시
    - `prompts/core/NPC_BEHAVIOR.md` — NPC 행동·자율성 규칙
    - `prompts/core/USER_IMPERSONATION_ALLOWED.md` — PC 묘사 허용 지침 (오타 파일 `FORBIDDEEN.md` 삭제)
    - `prompts/emotion/EMOTION.md` — Show-Don't-Tell 감정 엔진
    - `prompts/genre_specific/{daily,atmospheric,physical,tense,intimate}.md` — 씬 타입별 산문 규칙 스텁
    - `prompts/pov/POV_{1P,3P}_{CHAR,USER}_ANCHOR.md` — 시점별 서술 앵커 규칙
    - `prompts/style/STYLE_{1P,3P}.md` — 시점별 문체 스타일 가이드
- [신규] **actor.py 프롬프트 지문(fingerprint) 로깅 추가**
  - `src/agents/actor.py`
    - 매 턴 Actor 호출 전 `build_prompt_fingerprint()` / `format_prompt_fingerprint()` 호출
    - Fixed / Genre / Dynamic 프롬프트 길이와 히스토리 턴 수를 콘솔에 요약 출력
    - Gemini implicit cache 히트 여부를 디버깅할 때 Fixed 섹션 변경 여부를 즉시 확인 가능
- [버그픽스] **resolver.py Event ID 충돌 방지 및 LLM 응답 안정화**
  - `src/agents/resolver.py`
    - `_unique_event_id()` 추가: 같은 ID의 Event 노드가 이미 존재하면 `_2`, `_3` … 접미사를 붙여 충돌 회피. 100회 시도 실패 시 타임스탬프 접미사 폴백
    - LLM 호출에 `response_mime_type: "application/json"` 추가로 JSON 이외 출력 방지
    - event_id에 `need_name` 포함해 로그 식별성 향상 (`{loc}_{npc}_{need}_auto_{ts}` 형식)
- [신규] **default 세계관 범용 프롬프트 노드 추가**
  - `src/assets/worlds/default/schema.py`
    - `get_prompt_config()` 메서드 추가: perspective 인자를 받아 POV·섹션·씬별 설정 dict 반환
    - `get_full_config()` 반환값을 `prompt` 키 중심으로 재구성
    - 스키마 초기화 시 `Rule`, `SpeechProfile`, `RelationshipProfile` 예시 노드 + 관계 생성
    - `Location.home`에 `summary`, `prompt_hint`, `prompt_priority`, `tags` 필드 추가
- [개선] **DynamicState 타입 정규화 강화**
  - `src/core/database/helpers.py`
    - `DYNAMIC_STATE_INT_FIELDS`, `DYNAMIC_STATE_FLOAT_FIELDS`, `DYNAMIC_STATE_BOOL_FIELDS` 집합 추가
    - `_normalize_dynamic_state_updates()` 추가: 각 필드를 Kuzu 스키마 타입에 맞게 int / float / bool로 강제 변환. 변환 불가 값은 write 생략
    - `stress_level`, `workplace_stress_level`은 `normalize_stress_level()` 경유해 문자열 레이블도 정수로 매핑
- [개선] **extract_json_from_llm() JSON 파싱 강건성 향상**
  - `src/core/llm/client.py`
    - `_parse_json_candidate()` / `_iter_json_candidates()` 헬퍼 추가
    - 응답 전체에서 `{` / `[` 위치부터 JSON 후보를 순차 탐색해 첫 번째 유효 구조 반환
    - `JSONDecoder.raw_decode()` 우선 시도 후 실패 시 `rfind` 방식으로 폴백해 중첩 오류 복원
    - trailing comma / 미종결 문자열 보정은 기존과 동일하게 유지
- [버그픽스] **KuzuDB SET + $param 버그 우회 — events/manager.py**
  - `src/simulation/events/manager.py`
    - `set_flag()`: `SET gs.flags = $flags` 파라미터 방식 → JSON 리터럴 직접 삽입 방식으로 교체 (ooc_handler · time_plan과 동일 방식)
- [개선] **state/classifier.py stress_level 정규화 위임**
  - `src/simulation/state/classifier.py`
    - `_sanitize_stress_level()`: 인라인 매핑 로직 제거, `normalize_stress_level()` 위임으로 단순화
    - LLM 분류 프롬프트에 "JSON number from 0 to 10 ONLY" 문구 추가, 예시에 잘못된 경우 보완
- [정리] **assets/worlds/base.py 오타·legacy DDL 제거**
  - `"atmospheric.md"` 오타 키 → `"atmospheric"` 수정 (씬 타입 매핑 오류 해결)
  - `HAS_DYNAMIC_STATE` 관계 테이블 DDL 제거 — migration 기반 backfill(`HAS_STATE`)로 완전 대체

## 2026-05-17
- [버그픽스] **OOC 시간 처리 안정화**
  - `src/agents/prompt_factory/ooc_handler.py`
    - `_apply_time_change()` 반환값을 표준화해 시간 변경이 없을 때도 `time_before`, `time_after`, `elapsed_minutes`, `days_passed`를 항상 반환.
    - `new_datetime` 직접 지정, `time_delta_minutes`, `time_set` 처리 결과 모두 실제 적용된 경과 분과 날짜 경과 수를 계산하도록 정리.
    - `parse_ooc()` 결과에 `elapsed_minutes`, `days_passed`를 포함해 Manager 후속 시스템이 OOC 시간 이동을 재사용할 수 있게 변경.
  - `app.py`
    - OOC+RP 입력에서 Manager의 시간 DB write는 계속 억제하되, OOC로 이미 경과한 시간은 `needs_update`, `daily_systems`, static event 평가에 전달.
    - OOC step 출력에 `elapsed_minutes`, `days_passed`를 추가해 디버그에서 실제 시간 이동량을 확인 가능하게 변경.
    - 순수 `*OOC*` 입력은 OOC 적용 후 빈 RP 턴으로 Actor를 호출하지 않고 종료하도록 수정.
  - `src/agents/manager/effects.py`
    - `time_plan`이 없는 OOC 시간 패치에서도 `ooc_time_after`를 `current_dt`로 복원해 needs/daily/static event 처리 기준 시각으로 사용.
    - `elapsed_minutes=0`을 `1.0`으로 잘못 대체하지 않도록 needs update 경과 시간 처리 보정.

- [버그픽스] **OOC 위치 이동 일관성 보강**
  - `src/agents/prompt_factory/ooc_handler.py`
    - OOC 위치 이동 시 기존 `move_location()` 경로를 사용해 `LOCATED_AT` 관계와 `DynamicState.location_id`가 함께 갱신되도록 유지.
    - 이동 성공 시 `_set_global_location()`으로 `GlobalState.currentLocationId`까지 같은 위치로 동기화.
    - `moved_character_ids`를 OOC 결과에 포함해 그룹 이동 대상과 후속 보조 상태 업데이트 대상을 추적 가능하게 유지.

- [버그픽스] **Scene type downstream key 정규화**
  - `src/agents/context/scene_keys.py`
    - `normalize_scene_type()`, `normalize_scene_types()` 추가.
    - 매핑 규칙: `aggressive -> tense`, `vulnerable/bonding -> emotional`, `aegyo -> daily`, `formal -> formal`, 그 외는 자기 자신.
  - `src/agents/manager/planning.py`
    - classifier/rule 결과 scene type을 Manager downstream 진입 전에 정규화.
  - `src/agents/manager/queries.py`
    - `SCENE_REL_MAP`에 `formal` 기본 관계 매핑 추가.
    - 알 수 없는 scene key도 `_BASE_RELS`를 기본값으로 사용해 캐릭터 기본 정보가 누락되지 않도록 보강.
  - `src/agents/context/planner.py`
    - `ContextPlan.scene_type` 계산 시 정규화된 scene key 사용.
  - `src/agents/context/generic.py`
    - `Rule`, `SpeechProfile`, `RelationshipProfile` 조회 시 정규화된 scene key를 사용.

- [버그픽스] **Actor streaming fallback 복구**
  - `src/ui/actor_stream.py`
    - `recover_missing_analyze_prose()` 추가.
    - Actor 응답에서 `</analyze>`가 누락되어도 날짜 헤더가 있으면 헤더부터 본문으로 복구.
    - 날짜 헤더가 없으면 `<analyze>` 태그와 `CHARACTERS`, `PLAN`, `SCENE` 등 메타 지시문 라인을 제거한 뒤 남은 본문을 반환.
    - fallback 복구 여부를 `logs/actor_recovery.json`에 기록하고 콘솔 로그에도 `recovered_missing_analyze`로 출력.
    - `_extract_prose()`가 누락된 closing tag 상황에서도 빈 문자열 대신 복구된 본문을 반환하도록 변경.

- [검증] **안정화 smoke check 추가**
  - `scripts/smoke_arch_stabilization.py`
    - DB를 건드리지 않는 순수 smoke script 추가.
    - scene key 정규화, `*3시간 후*`, `*다음 날 아침*` OOC 시간 보정, Actor `</analyze>` 정상/누락 fallback 케이스를 검증.
  - 검증 완료
    - `python -m py_compile app.py src\agents\prompt_factory\ooc_handler.py src\agents\manager\planning.py src\agents\manager\queries.py src\agents\manager\effects.py src\agents\context\scene_keys.py src\agents\context\planner.py src\agents\context\generic.py src\ui\actor_stream.py scripts\smoke_arch_stabilization.py`
    - `python scripts\smoke_arch_stabilization.py`

## 2026-05-25
- [신규] **Actor 출력 blacklist guard 추가**
  - `src/ui/output_guard.py`, `src/agents/prompt_factory/prompts/blacklist/FORBIDDEN_TERMS.txt` 추가.
  - Actor 응답을 pending/history/DB commit 전에 검사하고, 금지어가 있으면 최대 2회 비공개 재생성.
  - 반복 위반 시 해당 응답을 커밋하지 않고 시스템 메시지로 차단 사유를 표시.
  - `src/ui/actor_stream.py`: `send_output=False` 경로를 추가해 guard 재시도 중에는 중간 응답이 UI에 노출되지 않도록 변경.

- [신규] **스레드별 유저노트 지원**
  - `data/threads/{thread_id}/usernote.md`를 매 턴 읽어 Dynamic prompt 최상단에 삽입.
  - `/help`에 유저노트 위치 안내 추가.

- [신규] **카카오톡/SNS 사이드 패널 기반 구현**
  - `src/simulation/systems/kakao/`, `src/ui/kakao_panel.py`, `public/elements/KakaoPanel.jsx`, `public/social_sidebar.js` 추가.
  - 톡방 생성, 메시지 큐잉, 초대, 최근 메시지 context 주입, SNS 피드 표시 기반을 구현.
  - 현재 기본 설정은 카카오톡/인스타그램 모두 강제 비활성화로 변경해 자동 실행되지 않도록 정리.

- [신규] **그래프 디버그 뷰어 확장**
  - `public/graph/` 정적 뷰어와 `src/ui/graph_loader.py`, `src/ui/graph_writer.py` 추가.
  - 최근 Event와 Memory 노드를 그래프에 표시하고, 스레드별 Kuzu DB 스냅샷을 직접 불러올 수 있게 변경.
  - `src/ui/graph_server.py`: 기본 진입점을 `ppt_viewer.html`로 변경.
  - `/debug graph` 갱신 시 현재 장면 중심 그래프에 Memory/Event 연결을 함께 반영.

- [신규] **시간 규칙 및 스케줄 tick 보강**
  - `src/simulation/systems/time_rules.py`: Rule 노드에서 시간/일정 관련 힌트를 찾아 Manager world context에 주입.
  - `src/simulation/systems/schedule_tick.py`: 턴 종료 시 시작된 NPC 스케줄을 감지해 off-scene NPC 위치를 이동하고 경량 Event를 생성.
  - `src/simulation/systems/needs/location_policy.py`: 욕구 해소 위치 후보를 need type과 스케줄 상태에 따라 제한.

- [개선] **Event / Memory 저장 방식 정리**
  - active Event가 끝난 턴의 Actor 응답까지 포함해 close summary를 갱신.
  - Event embedding 기준을 `narrative_summary`가 아니라 canonical `summary`로 단순화.
  - Memory는 Event summary 복제 대신 캐릭터별 주관 요약을 생성하고, 객관 summary는 `state_summary`에 보존.
  - `Event` 스키마에 `content`, `status`, `turn_count` 컬럼 추가.

- [개선] **상태/관계 업데이트 오염 방지**
  - `RELATIONSHIP.current_status`에 현재 자세, 행동, 장면 진행 묘사가 저장되지 않도록 압축/필터링.
  - 관계 affinity/trust 변화량 상한을 더 보수적으로 조정.
  - `DynamicInformation.sexual_information`은 현재 행위 묘사가 아니라 durable history summary만 저장하도록 프롬프트와 gating 정리.
  - NPC=PC self-state 경로에서도 scene partner가 있으면 Event 추출을 수행하도록 보강.

- [개선] **LLM JSON 응답 안정화**
  - JSON mime 호출에서는 thinking token이 출력 예산을 잠식하지 않도록 `thinking_budget=0`으로 정규화.
  - JSON 응답이 빈 텍스트로 돌아오면 diagnostics를 남기고 non-JSON streaming 경로로 재시도.

- [개선] **Needs trait 축 재설계**
  - 기존 단일 trait 목록을 16개 양극 trait-axis로 교체하고 cache version 갱신.
  - needs multiplier와 memory distortion hint가 새 trait 축을 사용하도록 변경.

- [버그픽스] **임신 감지 smoke check 추가 및 판정 보강**
  - `tests/smoke_pregnancy_manager.py` 추가.
  - 질내사정 감지에서 질문/회상/부정/콘돔/비질 삽입 케이스와 명시적 양성 케이스를 smoke check로 검증.

- [문서] **아키텍처 문서 추가**
  - `docs/ARCHITECTURE.md`, `docs/architecture_analysis.md`, `docs/architecture_validation.md`, `docs/generic_nodes_policy.md`, `docs/long_term_simulation_policy.md` 추가.
  - turn lifecycle, deferred commit, generic node, 장기 시뮬레이션 정책을 문서화.

- [프롬프트] **프롬프트 개선**
  - core/checklist/pov/style/emotion/intimate/blacklist 계열 프롬프트를 전반적으로 개선.
  - 혼합 입력 레이블링이 원문 순서를 보존하도록 수정.
  - world별 CoT append hook과 통합 blacklist 조립 경로를 보강.

## 2026-06-13
전체 감사(프론트/백엔드/3-에이전트) 기반 개선. Chainlit deprecated → 지원 스택은 정적 `frontend/app/` + `src/ui/web_app/`.

- `src/ui/web_app/actor.py`: DeepSeek 스트리밍 경로·`_is_deepseek_model`·`_openai_messages`·`httpx` import 제거(API 키 미보유).
- `src/ui/web_app/models.py`: `SUPPORTED_ACTOR_MODELS`/별칭에서 DeepSeek 제거(Gemini 3종+Claude 4종); 미호출 `WorldSelectionRequest` 제거; `ConversationState`에 `pending_ooc`·`narrative_turns` 필드 추가(JSON 영속).
- `src/ui/web_app/app.py`: reroll/tool/edit 엔드포인트 `RuntimeError`→HTTP 500 `{detail}` 표준화; 프론트 미호출 `POST /conversations/{id}/world` 라우트 제거.
- `src/ui/web_app/service.py`: `activate_variant`가 미커밋 `pending_commit.ai_response` 동기화(다음 턴 Updater 정합); `append_user_and_stream`가 임신/유기 OOC(`pending_ooc`)를 다음 턴 입력 앞에 주입(표시 메시지는 원본 유지).
- `src/ui/web_app/commit.py`: `commit_pending_web`에 임신 OOC→`pending_ooc` 저장 + narrative 압축 단계(10턴마다 `compress_to_narrative_log`) 이식(Chainlit 기능 포팅).
- `src/simulation/state/time_plan.py`: `commit_time_plan(companion_ids=...)`로 그룹 이동 시 동행 NPC도 이동하되 `_present_companion_ids()`로 실제 동석자만 이동(언급-only NPC 텔레포트 방지); `reconcile_location_with_prose` 추가 — Actor 산문 헤더 장소가 알려진 위치와 정확 매칭+현재와 다를 때만 산문 우선 보정.
- `src/agents/manager/effects.py`: `commit_manager_core_effects`가 scene NPC를 `commit_time_plan` 동행자로 전달.
- `src/simulation/state/multi_character.py`: `exited_character_ids` 추출→퇴장 NPC를 상위(`PART_OF`) 위치로 이동(presence 제외); `_personal_space_is_grounded` 강화(`{char_id}_house`는 목적지격+이동동사 조합일 때만 인정, 소유격/출발격/단순위치 제외).
- `src/core/database/helpers.py`: `move_location`이 성공/실패(`bool`) 반환.
- `src/ui/web_app/world_state.py`: `move_character_location`이 무효 위치에 `ValueError`(→엔드포인트 400).
- `src/simulation/state/updater.py`: `process_actor_response`가 상태 추출 전 `<analyze>` CoT 블록 제거(`[:N]` 절단이 CoT에 잠식되던 문제) + 주 updater 입력 캡 2000→4000.
- `frontend/app/app.js`: 죽은 DOM 참조(`activeConversationTitle/Preview`)·연쇄 고아(`makePreview`/`stripMarkdownForPreview`)·죽은 `.conversation-item` 리스너 제거; `alert()` 6곳→비차단 `showToast()`; ACTOR_MODELS에서 DeepSeek 제거.
- `frontend/app/style.css`: `.app-toast` 스타일 추가.
- `.env`·`example.env`: 미사용 `OPENROUTER_API_KEY`·`MODEL_CLASSIFIER_FALLBACK`·레거시 `NEO4J_*`·DeepSeek 항목 제거.
- `src/config.py`: DeepSeek 환경변수 제거.
- `CLAUDE.md`·`AGENTS.md`: Env 표에서 DeepSeek/OpenRouter 행 제거.
- `readme.md`: `client.py` 설명을 `Gemini / Vertex AI`로 정정.
- 설계 확정(코드 변경 없음): 호감도 trust 게이트(trust<90이면 양의 affinity 증가 차단)는 의도된 동작으로 유지.

## 2026-06-14
Chainlit 제거 + Codex 리뷰 지적 반영 + 백로그 정리.

- 삭제(Chainlit UI): 루트 `app.py`, `src/ui/{actor_stream,deferred_commit,response_editing,kakao_panel,status,session_world,time_state}.py`, `src/core/data_layer/`, `.chainlit/`, `chainlit.md`. 공유 모듈(graph_loader/graph_models/graph_writer/graph_server/debug_graph/pending_store/input_routing/output_guard/output_repair/session_models/social_media_settings/turn_debug/history)은 보존. (공유 인프라 5파일은 여전히 `import chainlit` → chainlit 패키지 의존성 유지, 완전 decoupling은 후속.)
- `.env`·`CLAUDE.md`·`AGENTS.md`: `CHAINLIT_AUTH_SECRET` 제거 + 두 문서의 Run/Env/턴 파이프라인/디렉토리·프로젝트 맵을 web_app 기준으로 갱신(Chainlit 참조 0건).
- `scripts/smoke_arch_stabilization.py`: `recover_missing_analyze_prose` import를 `src.ui.web_app.actor`로 repoint + 제거된 `_extract_prose` 검사 삭제(실행 통과).
- `tests/smoke_refactor_pending.py`: 삭제(전부 삭제된 Chainlit 모듈 테스트, obsolete).
- `src/ui/web_app/service.py` (Codex 지적 반영): `append_user_and_stream`를 try/finally(store.save)로 감싸 어느 단계 실패에도 상태 영속화 + `pending_ooc`는 `parse_ooc`가 DB에 즉시 반영하므로 파싱 성공 직후 소비(이중 반영·유실 방지); `_collect_generation(persist=False)`로 reroll 중복 저장 창 제거; `reroll_assistant` crash-recovery를 try/except 롤백으로 재작성(실패 시 rerolled 응답 제거 + history/recent를 messages에서 재구성한 일관 '폐기' 상태 저장 → resurrect·half-state·pending-없는-보이는-응답 모두 방지); 보류 커밋이 다른(과거) 응답의 것이면 reroll 거부 — 엉뚱한 pending 폐기와, 재생성이 `pending_commit`을 덮어써 디스크에 stale pending 파일이 남는 것을 모두 방지(`ValueError`→`app.py`에서 400); `_character_exists`로 무효 캐릭터 id도 400.
- `src/simulation/state/multi_character.py` (Codex 지적 반영): `exited_character_ids`만 있고 `character_updates`가 비어도 퇴장 처리하도록 조기 return 가드 수정; `_personal_space_is_grounded`가 "A는 B의 방에 들어갔다"를 A 자택으로 오인하던 것 수정(중간 소유격 '의' 차단).
- `src/ui/web_app/commit.py` (Codex 지적 반영): `_maybe_compress_narrative`가 압축 성공 시에만 버퍼를 비우도록 변경(실패 시 다음 턴 재시도).
- `frontend/app/app.js`: `loadWorldProfiles`를 try/catch로 감싸 `/api/worlds` 실패 시 토스트(폴백 유지, 앱 초기화 중단 방지).
- `src/agents/manager/pipeline.py`: 리더(루트 app.py)가 삭제되어 죽은 `manager_effects["kakao_panel_refresh"]` write 제거.
- `src/core/database/driver.py`: `_resolve_driver`에서 Chainlit 세션 드라이버 폴백(`cl.user_session.get("db_driver")`) 제거 — 활성 ContextVar 드라이버(없으면 기본) 사용.
- `src/ui/pending_store.py`: `_current_thread_id`의 `cl.context.session.thread_id` 제거(web UI는 pending dict의 thread_id 사용 → ambient 없음, "").
- `src/ui/debug_graph.py`: 모듈 `import chainlit` 제거; `_pending_time_state`·thread_id 조회의 `cl` 사용 제거; Chainlit 전용 `send_debug_graph`(`cl.Message`) 삭제(`upsert_debug_graph` 유지).
- `src/simulation/systems/social/graph.py`: `_cache_key`의 `cl.user_session.get("db_path")` 제거 → `current_db_path()`(스레드/대화별 활성 DB 경로)를 키로 사용. 전역 "__global__" 키로 인한 스레드 간 캐릭터 캐시 오염 해소(활성 드라이버 없으면 "__global__" 폴백).
- `src/simulation/systems/needs/traits.py`: `_trait_cache_context`의 `cl.user_session` world/scenario 조회 제거 → config `WORLD_ID`/기본 시나리오 사용.
- chainlit 완전 decoupling 완료: 전 코드 `import chainlit` 0건(compileall·import 검증), chainlit는 더 이상 코드 의존성 아님.
- `src/core/database/driver.py`: `KuzuAsyncDriver.db_path` 속성 + `current_db_path()` 추가(기본 드라이버를 강제 생성하지 않고 ContextVar만 확인 — 스레드별 캐시 격리 키 용도).
- `src/ui/web_app/actor.py`: Gemini `finish_reason`·Claude `stop_reason`를 캡처해 토큰 한도 절단 시 경고 로그(silent truncation 방지).
- `frontend/world_editor.html`: `@media (max-width:760px)` 추가 — nav/`prompt-layout`/`editor-split`/`rel-layout`/tree를 모바일에서 단일 컬럼으로 스택.
- `tests/smoke_web_app_state.py`: web_app 상태 로직(모델 정규화·variant pending 동기화·삭제 시 pending 폐기·preview) DB·LLM 없는 smoke 검사 추가(`get_client` stub).

## 2026-06-15

- [신규] `src/simulation/systems/social/models.py`: `StubProfile` Pydantic 모델 추가 — LLM stub 출력 검증·정규화용 (`extra="ignore"`, `coerce_numbers_to_str=True`).
- [개선] `src/simulation/systems/social/graph.py` — `_fill_plausible_stub_fields`: `biological_sex`·`age`를 먼저 확정 후 height/weight/measurements/physique를 내부 일관되게 생성하도록 앵커 순서 명시. `_create_stub`: `StubProfile`로 LLM 출력 검증·정규화 후 빈 stub 필드만 채움(관찰 증거 보존); `DynamicInformation` props에 `age`·`measurements`·`biological_sex` 추가.
- [신규] `src/simulation/state/importance.py`: 단일 루브릭 상수 `IMPORTANCE_RUBRIC` 정의 — 첫 경험·첫 고백·중요 인물 첫 만남을 8-10으로 상향(기존 5-7 혼재), 반복·일상 사건 5-7 이하 명확 구분.
- [개선] `IMPORTANCE_RUBRIC` 공유 import 교체: `updater.py`, `events.py`, `turn_extractor.py` (turn_extractor는 루브릭 누락 상태였음 → 추가).
- [버그픽스] `src/apps/app/output_guard.py`: `parents[1] / "prompt"` → `parents[2] / "prompts"` 경로 수정 — `FORBIDDEN_TERMS.txt`(66개 패턴) 로드 실패로 가드가 조용히 비활성화되던 버그 해소.
- [버그픽스] `src/apps/app/service.py`: `output_repair`가 `full_response` 대신 `visible_text`만 검사·수정 — `<analyze>` 내 1인칭 추론으로 인한 오발동 제거.
- [개선] `src/agents/prompt_factory/prompts/blacklist/BLACKLIST.md`: "Comparison and Simile Prohibition" 섹션 추가 — `마치 ~ 같았다/처럼 느껴졌다` 등 구조 금지 Actor에 명시(기존: 단어 목록만 있어 Actor가 자유롭게 사용 → output_repair 100% 발동 원인); `실무적인` 단어 목록에 추가.
- [개선] `src/simulation/state/updater.py`: `apply_scene_relationship_updates`를 `asyncio.create_task`로 병렬화(턴당 순차 ~7s 제거); `try/finally`로 auxiliary 실패 시 relationship 태스크 고아 방지.
- [신규] `src/core/llm/client.py`: per-call 지연 로그(`logs/llm_latency.jsonl`) 추가 — ts/log_source/model/elapsed_ms/status.
- [신규] `scripts/analyze_llm_latency.py`: log_source별 평균/최대 지연 + 턴 클러스터 순차/병렬 요약 스크립트.
- [리팩토링] `simulation/state/extract/` (신규 패키지): `turn_extractor.py`, `multi_character.py`, `dynamic_information.py`, `creator_slots.py` + `__init__.py` 공개 API 재노출.
- [리팩토링] `simulation/state/apply/` (신규 패키지): `events.py`, `relationships.py`, `audit.py`, `update_policy.py`, `time_plan.py` + `__init__.py` 공개 API 재노출.
- [리팩토링] `simulation/systems/world_dynamics/` (신규 패키지): `organic.py`, `personality.py`, `reputation.py` + `__init__.py`.
- [리팩토링] `simulation/systems/scheduling/` (신규 패키지): `schedules.py`, `schedule_tick.py`, `time_rules.py` + `__init__.py`; 이동된 모듈 경로로 caller import 전면 갱신(`updater.py` 외 12개 파일); `CLAUDE.md` 디렉토리 맵 갱신.
- [신규] Web UI 5항목: OOC 설정 모달(스레드별 저장), 유저노트 CRUD(다중 노트), 모델 버튼 중앙 정렬, 시나리오 레이블, textarea 자동 리사이즈.
- [신규] `src/simulation/systems/memory/gate.py`: Memory Gate 구현 — `GateDecision` enum, `decide_gate`(순수 룰: importance<5 + 신호 없음=REJECT), `apply_gate`(DB dedup + 올바른 target mem_id 반환).
- [신규] `src/core/database/driver.py` `_COLUMN_MIGRATIONS` +12: Memory 11개 신규 컬럼(status/source_commit_id/source_type/confidence/signals/salience/recall_count/last_recalled_at/reinforced_count/last_reinforced_at/resolved_at) + `Event.source_commit_id` 자동 마이그레이션.
- [개선] `src/simulation/systems/memory/__init__.py`: Gate 통합, 한국어 키워드 기반 signal 자동 추론(`_infer_signals_from_summary`), 2-phase commit(게이트 결정 후 일괄 DB write), Memory 신규 컬럼 쓰기 + REINFORCE 경로 실제 matched mem_id 사용.
- [개선] `src/simulation/state/extract/turn_extractor.py`: `signals`·`source_type`·`suggested_memory_type` 추출 추가; `PROMPT_VERSION` v3으로 캐시 무효화.
- [개선] `src/agents/manager/core_context.py`: `_recall_relevant_memories`를 3 독립 try 블록(pinned cap 2/recent/vector)으로 분리; pre-migration NULL status 호환 필터; confidence_label 추가.
- [버그픽스] `src/simulation/state/apply/events.py`: `commit_id` 기반 idempotency — 같은 commit이 이미 Event를 생성했으면 Event 재생성 스킵 + `ensure_memories_for_event` 재호출(Event 생성 성공 후 Memory 생성 실패 시 retry에서 Memory 영구 누락 방지); Event CREATE에 `source_commit_id` 컬럼 추가.

## 2026-06-16

- [리팩토링] **Phase D 대형 파일 분리** — 4개 파일 300줄 이하로 축소, 3개 신규 파일 생성.
  - `src/core/database/migrations.py` 신규: `_TABLE_MIGRATIONS`, `_COLUMN_MIGRATIONS`, `_DATA_PATCHES` 리스트를 `driver.py`에서 분리. Secret-Character HAS_SECRET backfill 패치 추가. `driver.py` 620L → ~370L.
  - `src/apps/app/message_ops.py` 신규: `reroll_assistant`, `edit_message`, `activate_variant`, `delete_message` 4개 함수를 `service.py`에서 분리. `service.py`와의 순환 참조 방지를 위해 `_message_ops_payload`·`_preview`·`_generate` 내부 래퍼에 lazy import 사용. `service.py` 778L → ~520L.
  - `src/simulation/state/extract/primary.py` 신규: `_run_primary_update`, `_render_state_world_context`, `_render_dynamic_state_field_policy`, `_compact_world_context_text`를 `updater.py`에서 분리. `updater.py` 964L → ~780L.
  - `src/simulation/systems/memory/__init__.py`: decay 로직(이미 `decay.py`에 존재)·`_compress_memories_batch` 제거 → 메모리 생성(`ensure_memories_for_event`) 전용으로 축소 487L → ~220L; `decay.run_decay`·`distortion.distort_on_affinity_change` re-export 유지.
  - `src/apps/app/app.py`: import를 `service`와 `message_ops` 두 모듈로 분리.
  - `CLAUDE.md` 디렉토리 맵: `migrations.py`, `primary.py`, `message_ops.py`, `decay.py(기존)` 항목 추가; `service.py` 설명 갱신.
  - `AGENTS.md` Project Map·Where To Change Things 갱신: 신규 4개 파일 반영, reroll/edit/activate/delete → `message_ops.py` 행 추가.

- [아키텍처 감사 개선] **core/·simulation/ 감사 로드맵 Phase A/B/D 구현** (Codex 적대적 리뷰로 단계별 게이팅).
  - **Phase A — 트랜잭션·실패 시맨틱:**
    - `core/database/session.py` `KuzuTransaction` 추가(BEGIN→COMMIT 동안 락 유지, 오류 시 ROLLBACK, 재진입 불가). `driver.py`·`proxy.py`에 `transaction()` 위임.
    - `helpers.py` `update_global_flags`/`set_global_flag`(원자적 read-modify-write 머지) — `events/manager.py`·`memory/narrative.py`의 손수 escape를 폐기(lost-update 해소). `move_location`·`update_dynamic_state`·`update_relationship_affinity`·`get_dynamic_state_field_types`를 `tx=` 인자로 외부 트랜잭션에 합류 가능하게(`_executor` 컨텍스트).
    - 다중 쓰기 핫스팟 원자화: 캐릭터별 메모리 생성(임베딩은 트랜잭션 밖 선계산), Kakao `_store_message`, 양방향 affinity, `multi_character` 캐릭터별 루프, 이벤트 생성(`apply/events.py`).
    - `core/llm/errors.py` 신규(`LLMError`/`TransientLLMError(=TimeoutError)`/`LLMJsonError`); `client.py` 스트리밍 타임아웃 + 제한적 재시도/백오프, `extract_json_from_llm(strict=)` 추가(추출 실패와 정상 빈 결과 구분).
    - reroll 일관성: 커밋된 응답 reroll 거부(서버 가드 + 프론트 원복).
    - 테스트: `tests/smoke_db_transaction.py`, `tests/smoke_llm_client.py`.
  - **Phase B — 타입 경계(핵심 슬라이스):**
    - `simulation/systems/needs/models.py` 신규(`NeedLevels` + 정본 `NEED_DEFAULTS`/`SETTLE_LEVELS`). `resolver→needs` 역방향 import 레이어 정리(needs가 단일 출처, resolver 함수는 needs 내부에서 지연 import로 사이클 차단).
    - `turn_extractor.py` `_synthesize_event_id` 추가 — unified 모드에서 event id 누락으로 `_create_event`가 조기 반환해 이벤트가 조용히 누락되던 버그 수정(기본 legacy 경로 영향 없음).
    - message_ops edit/delete/activate의 커밋-턴 drift는 **WONTFIX**로 결정(자유로운 히스토리 편집 유지; 실제 그래프 롤백/브랜칭 도입 전까지 허용된 한계).
  - **Phase D — 견고성 폴리시:**
    - `config.py` `EMBEDDING_DIM`을 `int|None`으로 파싱(빈 값만 None; 형식 오류·비양수는 import 시 즉시 실패), `HF_TOKEN` 형식 검증(`hf_` 접두사). `encoder.py` 임베딩 싱글톤에 `threading.Lock` 이중검사 잠금(executor 스레드 동시 첫 호출 이중 적재 방지). `schema_builder.py`의 `agents` 상향 import를 `__main__` 안으로 이동(모듈 import 시 레이어 위반 제거).
    - 메모리 라이프사이클 정리: `memory/__init__.py`에 상태머신(create→reinforce→distort(즉시)→decay(시간)→narrative) 순서·불변식 문서화. `distort_on_affinity_change`→`AffinityDistortReport`, `run_decay`→`DecayReport`(버킷별 카운트 + `llm_failed`) 신호 반환; 배치 LLM 헬퍼는 경성 실패 시 `None`(정상 no-op `{}`와 구분, `strict=True` 파싱) → 호출부(`updater.py`·`manager/effects.py`)가 `llm_failed` 로깅.
    - 마이그레이션 견고화: `migrations.py` `MigrationOp`/`migration_ops()`로 DDL을 파싱해 **노드→rel→컬럼** 순서 적용(rel이 노드보다 먼저 실행돼 영구 누락되던 순서 버그 해소). `driver.py` `_run_migrations`를 `SchemaMigration` 원장 + `table_info`/`show_tables` introspection 기반으로 재작성(에러 문자열 매칭 폐지; endpoint 없는 rel은 다음 기동에 재시도하도록 보류, 진짜 실패는 로깅).
  - Codex 리뷰 수정: `EMBEDDING_DIM` 무성 1024 fallback → 빠른 실패로 전환; 메모리 배치 헬퍼가 파싱 실패(`{}`)를 no-op으로 삼키던 문제 → `strict=True` + 비-list 시 `None`.
  - `CLAUDE.md`/`AGENTS.md`: 트랜잭션 규칙·`errors.py`·`needs/models.py`·`migrations.py` 파서/원장·`driver.py` introspection 항목 동기화.

