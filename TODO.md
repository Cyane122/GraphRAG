# GraphRAG RP Chatbot Architecture Stabilization TODO

> 기준 문서: `docs/architecture_analysis.md`
> 목표: 현재 GraphRAG 기반 장기 RP 챗봇 엔진의 구조적 불일치, 상태 변경 혼선, 장기 유지보수 리스크를 줄인다.

## 현재 판단

이 TODO는 새 기능 추가보다 먼저 엔진의 내부 표준을 안정화하는 작업 목록이다.

현재 코드 기준으로 `OOC` 시간 변경 로직은 일부 구현되어 있다. 따라서 `OOC 시간 처리`는 신규 구현이 아니라 **검증, 반환값 개선, Manager time plan과의 충돌 정리**가 핵심이다.

또한 `AGENTS.md`에는 예전 모듈 경로와 schema builder 명령이 남아 있다. 문서만 보고 실행하거나 작업할 때 실패할 수 있으므로 문서 동기화를 Priority 0에 둔다.

## Priority 0 - 즉시 정리할 불일치

### 0.1 OOC 시간 처리 검증 및 안정화

현재 `src/agents/prompt_factory/ooc_handler.py`에는 `time_delta_minutes`, `time_set`, `_apply_time_change()`가 있으며 `GlobalState.currentTime`을 갱신한다. 남은 작업은 적용 결과를 명확히 기록하고, OOC+RP 혼합 입력에서 Manager time plan과 충돌하지 않게 만드는 것이다.

- [ ] `parse_ooc()` 반환값에 시간 변경 정보를 포함한다.
  - [ ] `time_changed`
  - [ ] `time_before`
  - [ ] `time_after`
  - [ ] `applied_time_delta_minutes`
  - [ ] `applied_time_set`
- [ ] Chainlit OOC step에서 시간 변경 내용을 표시한다.
- [ ] `*3시간 뒤*` 입력 시 DB 시간이 실제로 3시간 증가하는지 검증한다.
- [ ] `*다음 날 아침*` 입력 시 날짜/시간이 자연스럽게 변경되는지 검증한다.
- [ ] OOC-only 입력과 OOC+RP 혼합 입력 양쪽에서 동작을 확인한다.
- [ ] OOC 시간 변경 후 다음 RP 응답의 turn header와 dynamic prompt가 변경된 시간을 반영하는지 확인한다.
- [ ] OOC+RP 혼합 입력에서 OOC time patch와 `manager_effects.time_plan`이 시간을 이중 증가시키지 않도록 정책을 정한다.
- [ ] 예외 케이스를 정리한다.
  - [ ] 잘못된 시간 표현
  - [ ] 과도한 시간 증가량
  - [ ] 날짜 역행
  - [ ] `GlobalState.currentTime` 포맷 불일치
- [ ] `currentTime` 파싱 실패 시 `datetime.now()`로 조용히 fallback하는 현재 정책을 재검토한다.

완료 기준:

- [ ] `*3시간 뒤*` 입력 시 DB 시간이 실제로 3시간 증가한다.
- [ ] `*다음 날 아침*` 입력 시 날짜와 시간이 기대한 값으로 변경된다.
- [ ] OOC 시간 변경이 UI step, turn debug, 다음 dynamic prompt에서 확인된다.
- [ ] OOC+RP 혼합 입력에서 시간 증가가 중복 적용되지 않는다.

### 0.2 `HAS_STATE` / `HAS_DYNAMIC_STATE` 관계 정리

공용 코드는 대부분 `HAS_STATE`를 사용한다. `HAS_DYNAMIC_STATE`는 현재 `src/assets/worlds/base.py`에서 relation table 생성 흔적이 남아 있는 legacy 관계로 보인다.

- [x] 전체 코드에서 `HAS_DYNAMIC_STATE` 사용처를 재확인한다.
- [x] 신규 코드 표준 관계를 `HAS_STATE`로 확정한다.
- [x] `src/assets/worlds/base.py`에서 신규 `HAS_DYNAMIC_STATE` 생성을 중단하거나 deprecated 주석을 단다.
- [x] 기존 DB migration helper를 작성한다.
  - [x] `HAS_DYNAMIC_STATE`만 있는 경우 `HAS_STATE` 생성
  - [x] 양쪽 모두 있는 경우 중복 여부 확인
- [x] 기존 월드 DB 호환성을 위해 legacy read fallback이 필요한지 결정한다.
- [x] 관련 문서와 주석을 수정한다.

완료 기준:

- [x] 공용 코드의 DynamicState 조회는 전부 `HAS_STATE` 기준으로 작동한다.
- [x] 기존 월드 DB에서도 DynamicState 조회가 깨지지 않는다.
- [x] 새 월드 생성 시 `HAS_DYNAMIC_STATE`가 새로 생성되지 않는다.

### 0.3 프로젝트 문서와 실제 경로 동기화

`AGENTS.md`에는 현재 코드와 맞지 않는 예전 경로가 남아 있다. schema builder 명령뿐 아니라 모듈 책임 표도 현재 구조로 갱신해야 한다.

- [x] `AGENTS.md`, `README.md`, `changelog.md`, `docs/`에서 schema builder 명령을 검색한다.
- [x] 예전 명령을 제거하거나 deprecated로 표기한다.

현재 명령:

```bash
python -m src.core.database.schema_builder
```

- [x] `AGENTS.md`의 Module Responsibilities를 현재 경로 기준으로 수정한다.
  - [x] `src/agents/manager.py`
  - [x] `src/agents/manager_pipeline.py`
  - [x] `src/agents/prompt_factory/builder.py`
  - [x] `src/agents/prompt_factory/ooc_handler.py`
  - [x] `src/core/database/helpers.py`
  - [x] `src/core/database/schema_builder.py`
  - [x] `src/simulation/state/updater.py`
  - [x] `src/simulation/systems/*`
- [x] 문서의 Neo4j/Kuzu 표현을 실제 사용 DB 용어와 맞춘다.
- [x] 실행 명령만 보고 schema 초기화가 가능한지 확인한다.

완료 기준:

- [x] 문서만 보고 실행해도 schema 초기화 명령이 실패하지 않는다.
- [x] 새 작업자가 문서의 모듈 경로를 따라가도 실제 파일을 찾을 수 있다.

## Priority 1 - 내부 표준화

### 1.1 Scene type canonicalization 추가

현재 scene type 문자열은 classifier, manager query, context planner, generic context, prompt builder, few-shot key에서 직접 사용된다. 월드별 scene type이 확장될수록 내부 조회가 빠질 위험이 크다.

- [ ] 현재 사용 중인 scene type을 전수 조사한다.
  - [ ] `World.SCENE_TYPES`
  - [ ] `SCENE_REL_MAP`
  - [ ] `ContextPlanner`
  - [ ] `PromptBuilder`
  - [ ] `generic_context`
  - [ ] few-shot scene key
  - [ ] genre prompt key
- [ ] `src/agents/scene_types.py` 또는 동등한 모듈을 추가한다.
- [ ] 내부 표준 `canonical_scene_type`을 정의한다.
- [ ] world-specific scene type을 canonical type으로 매핑한다.
- [ ] classifier 출력값에 normalization 단계를 추가한다.
- [ ] `raw_scene_types`와 `canonical_scene_types`를 분리한다.
- [ ] profile fetch, memory recall, social context, generic context는 canonical type 기준으로 작동하게 수정한다.
- [ ] prompt에는 필요 시 raw scene type도 함께 노출한다.
- [ ] `SCENE_REL_MAP`은 canonical type 기준으로만 조회되게 한다.
- [ ] generic context 조회는 canonical 우선, raw fallback 필요 여부를 결정한다.

초기 매핑 예시:

| Raw Scene Type | Canonical Scene Type |
| --- | --- |
| `daily` | `daily` |
| `bonding` | `emotional` |
| `vulnerable` | `emotional` |
| `formal` | `formal` |
| `tense` | `tense` |
| `aggressive` | `tense` |
| `physical` | `physical` |
| `workplace` | `workplace` |
| `aegyo` | `daily` |
| `intimate` | `intimate` |

완료 기준:

- [ ] 어떤 월드가 scene type을 확장해도 내부 시스템은 canonical type 기준으로 안정적으로 작동한다.
- [ ] `SCENE_REL_MAP` 누락으로 profile fetch가 빠지는 일이 없다.
- [ ] scene type 변경이 memory/social/goal/item/secret trigger에 일관되게 반영된다.

### 1.2 State Mutation Ownership 문서 작성

여러 시스템이 같은 DB 필드에 접근하므로, 각 상태의 소유권을 명시한다. 목표는 “어떤 필드는 누가 바꿀 수 있는가?”를 문서만 보고 판단할 수 있게 만드는 것이다.

- [ ] `docs/state_mutation_ownership.md` 생성
- [ ] 주요 node/edge별 owner system 정의
- [ ] 직접 수정 가능 여부와 예외 조건 정의
- [ ] audit 필요 필드 표시
- [ ] LLM updater가 수정 가능한 필드 allowlist 재검토
- [ ] OOC가 수정 가능한 필드와 수정 불가 필드를 구분한다.
- [ ] Manager effect가 수정 가능한 필드와 Actor postprocess가 수정 가능한 필드를 분리한다.

초안:

| Target | Field / Relation | Owner | Other Writers | Policy |
| --- | --- | --- | --- | --- |
| `GlobalState` | `currentTime` | Manager/OOC | None | Actor updater 직접 수정 금지 |
| `GlobalState` | `weather` | Manager/OOC | Static scheduler optional | audit 필요 |
| `DynamicState` | `location_id` | Manager/OOC | Actor updater 제한적 허용 | location move helper 사용 |
| `DynamicState` | `mood` | Actor updater | OOC | evidence 필요 |
| `NeedsState` | all | Needs system | None | 외부 직접 수정 금지 |
| `Relationship` | `affinity` | State updater | Reputation | delta log 필요 |
| `Memory` | `summary_level` | Memory system | None | decay 외 수정 금지 |
| `Secret` | `status` | Secret system | None | Actor 직접 reveal 금지 |
| `Goal` | `progress/status` | Goal system | OOC optional | event 연결 필요 |
| `Item` | `owner_id/location_id` | Item system | OOC optional | existing item만 수정 |

완료 기준:

- [ ] 새 시스템을 추가할 때 어느 필드를 수정해도 되는지 문서만 보고 판단 가능하다.
- [ ] LLM updater allowlist와 ownership 문서가 충돌하지 않는다.

### 1.3 State mutation logging 최소 골격 추가

상태 변경 추적성을 높인다. 단, ownership 문서가 먼저 있어야 로그의 의미가 생긴다.

- [ ] mutation log 저장 위치와 형식을 정한다.
- [ ] 공통 log helper를 추가한다.
- [ ] log payload에 최소 필드를 포함한다.
  - [ ] turn id
  - [ ] system name
  - [ ] target node/edge
  - [ ] field
  - [ ] before
  - [ ] after
  - [ ] reason
  - [ ] evidence text
- [ ] OOC patch를 mutation log에 포함한다.
- [ ] manager effects commit을 mutation log에 포함한다.
- [ ] actor postprocess mutation을 mutation log에 포함한다.

완료 기준:

- [ ] 특정 캐릭터의 mood, location, affinity 변화 이력을 추적할 수 있다.
- [ ] 잘못된 업데이트가 발생했을 때 원인 시스템을 찾을 수 있다.

## Priority 2 - Actor / Prompt 안정화

### 2.1 Actor streaming / non-streaming 공통화

현재 Chainlit main path는 streaming path를 사용하고, `src/agents/actor.py`는 non-streaming 테스트/레거시용에 가깝다. 요청 조립과 응답 파싱이 갈라지면 장기적으로 버그가 생길 수 있다.

- [ ] `ActorRunner` 또는 유사 공통 레이어를 설계한다.
- [ ] 공통화 대상을 정의한다.
  - [ ] system instruction 조립
  - [ ] history 변환
  - [ ] dynamic prompt 삽입
  - [ ] prefill 처리
  - [ ] analyze block 제거
  - [ ] scene character list 추출
  - [ ] final prose 추출
- [ ] streaming path가 `ActorRunner.stream()`을 사용하게 한다.
- [ ] non-streaming path가 `ActorRunner.generate()`를 사용하게 한다.
- [ ] 기존 `src/agents/actor.py`의 역할을 재정의한다.

완료 기준:

- [ ] streaming과 non-streaming이 동일한 prompt/request 구성 규칙을 사용한다.
- [ ] 응답 파싱 로직이 한 곳에 모인다.

### 2.2 `<analyze>` block fallback 처리

Actor streaming은 `<analyze>` prefill을 사용하고, `</analyze>` 이전 텍스트를 UI에 표시하지 않는다. 모델이 닫는 태그를 누락할 경우 출력이 숨겨질 수 있다.

- [ ] `</analyze>` 미출현 시 fallback 정책을 정의한다.
- [ ] 일정 문자 수 또는 줄 수 이후 강제 prose 전환을 검토한다.
- [ ] 최종 응답에서 잔여 `<analyze>` 태그를 제거한다.
- [ ] analyze block이 없는 응답도 정상 처리한다.
- [ ] prompt leak guard와 중복/충돌 여부를 확인한다.

완료 기준:

- [ ] 모델이 `</analyze>`를 누락해도 사용자에게 빈 응답이 표시되지 않는다.
- [ ] analyze 텍스트가 prose 영역으로 누출될 확률이 줄어든다.

### 2.3 PromptBuilder 출력 디버그 스냅샷 개선

Prompt 조립 품질을 확인하기 쉽게 만든다.

- [ ] turn별 Fixed/Genre/Dynamic prompt snapshot 저장 옵션을 정리한다.
- [ ] 민감 정보 포함 여부를 확인한다.
- [ ] dynamic context block별 token/문자 수를 기록한다.
- [ ] 어떤 context source가 선택되었는지 기록한다.
- [ ] ContextPlan decision reason을 기록한다.

완료 기준:

- [ ] 특정 턴에서 왜 특정 memory/secret/item이 prompt에 들어갔는지 확인할 수 있다.
- [ ] prompt 비대화 원인을 block 단위로 확인할 수 있다.

## Priority 3 - Context Retrieval 품질 개선

### 3.1 Memory recall reason 추가

- [ ] memory recall 결과에 `reason_for_inclusion` 추가
- [ ] recall source 구분
  - [ ] vector similarity
  - [ ] same location
  - [ ] same character
  - [ ] same item
  - [ ] emotional relevance
  - [ ] recent event
- [ ] 오래된 기억의 사용 방식 hint 추가
  - [ ] 직접 언급 가능
  - [ ] 분위기 반영만 권장
  - [ ] 왜곡 가능성 있음
- [ ] context renderer에서 reason을 compact하게 출력

완료 기준:

- [ ] Actor가 memory를 무작정 반복 언급하지 않고, 장면에 맞게 활용한다.
- [ ] recall된 memory가 왜 들어갔는지 debug 가능하다.

### 3.2 ContextPlanner rule 정리

- [ ] trigger keyword 목록을 파일/상수로 분리한다.
- [ ] scene type 기반 trigger와 user input 기반 trigger를 분리한다.
- [ ] importance 기반 trigger 기준을 재검토한다.
- [ ] memory/social/item/goal/secret 각각의 enable condition을 문서화한다.
- [ ] false positive가 많은 키워드를 제거한다.
- [ ] false negative가 많은 장면을 보강한다.

완료 기준:

- [ ] 어떤 입력에서 어떤 context system이 켜지는지 예측 가능하다.
- [ ] 불필요한 memory/social/item context가 과다 삽입되지 않는다.

### 3.3 Generic prompt node priority 정리

- [ ] 각 generic node의 기본 budget을 재검토한다.
- [ ] `prompt_priority` 정렬 기준을 확인한다.
- [ ] 같은 역할의 hint가 중복 삽입되는지 확인한다.
- [ ] `SpeechProfile` fallback 규칙을 문서화한다.
- [ ] `RelationshipProfile` scene type fallback 규칙을 추가한다.

완료 기준:

- [ ] dynamic context가 중복 없이 compact하게 유지된다.
- [ ] 말투/관계 hint가 scene type 누락으로 빠지지 않는다.

## Priority 4 - Postprocess 구조 개선

### 4.1 `process_actor_response()`를 단계별 handler로 분리

- [ ] 현재 처리 순서를 문서화한다.
- [ ] handler 인터페이스 초안을 작성한다.
- [ ] 각 handler 입력/출력을 정의한다.
- [ ] 다음 단위로 분리 검토
  - [ ] GuardHandler
  - [ ] CombinedUpdateHandler
  - [ ] DynamicStateHandler
  - [ ] RelationshipHandler
  - [ ] EventMemoryHandler
  - [ ] SocialGraphHandler
  - [ ] ReputationHandler
  - [ ] PersonalityDriftHandler
  - [ ] GoalHandler
  - [ ] ItemHandler
  - [ ] SecretHandler
  - [ ] OrganicHandler
- [ ] handler별 unit test 작성

완료 기준:

- [ ] 한 handler를 수정해도 다른 후처리 시스템에 미치는 영향이 줄어든다.
- [ ] 후처리 순서를 명시적으로 확인할 수 있다.

### 4.2 MutationPlan 도입 검토

각 시스템이 직접 DB를 쓰는 대신, 변경 계획을 반환하고 commit layer가 적용하는 구조를 검토한다.

- [ ] `MutationPlan` 타입 초안 작성
- [ ] mutation 종류 정의
  - [ ] dynamic_state_update
  - [ ] relationship_delta
  - [ ] event_create
  - [ ] memory_create
  - [ ] location_move
  - [ ] goal_update
  - [ ] item_update
  - [ ] secret_update
  - [ ] personality_update
- [ ] conflict detection 규칙 정의
- [ ] mutation audit log와 연결
- [ ] 우선 일부 시스템에만 시범 적용
  - [ ] Relationship
  - [ ] DynamicState
  - [ ] Event/Memory

완료 기준:

- [ ] 복수 시스템이 같은 필드를 바꾸려 할 때 충돌을 감지할 수 있다.
- [ ] 모든 상태 변경을 한 곳에서 기록/검증할 수 있다.

## Priority 5 - Schema / Props 정리

### 5.1 `props` JSON schema version 도입

- [ ] `props_schema_version` 도입 여부 결정
- [ ] `StaticProfile.props` 표준 key 정의
- [ ] `Personality.props` 표준 key 정의
- [ ] `IntimateProfile.props` 표준 key 정의
- [ ] `WorkplaceProfile.props` 표준 key 정의
- [ ] 알 수 없는 key 처리 정책 정의
- [ ] props migration helper 작성

완료 기준:

- [ ] world-specific props를 유지하면서도 공용 코드가 기대하는 key를 안정적으로 읽을 수 있다.
- [ ] 성격 drift나 trait 생성 시 엉뚱한 key가 무분별하게 늘어나지 않는다.

### 5.2 공통 필드 column화 검토

자주 조회하거나 시스템 분기에 쓰는 필드는 JSON blob이 아니라 column으로 빼는 것을 검토한다.

후보:

- [ ] `Personality.speech_style`
- [ ] `Personality.core_traits`
- [ ] `StaticProfile.role`
- [ ] `StaticProfile.age`
- [ ] `StaticProfile.gender`
- [ ] `Personality.last_drifted_at`
- [ ] `Personality.macro_drift_count`

완료 기준:

- [ ] 자주 쓰는 공통 필드는 검색/마이그레이션이 쉬워진다.
- [ ] 월드별 자유도는 유지된다.

## Priority 6 - 테스트 / 회귀 방지

### 6.1 핵심 턴 플로우 테스트 작성

- [ ] 일반 RP 1턴 생성
- [ ] 다음 입력 시 pending commit 확정
- [ ] reroll 시 pending commit 폐기
- [ ] edit 시 pending response 수정
- [ ] OOC-only 입력
- [ ] OOC+RP 혼합 입력
- [ ] 시간 경과 입력
- [ ] 장소 이동 입력
- [ ] memory recall 발생 입력
- [ ] secret hint 발생 입력
- [ ] item 언급 입력
- [ ] goal 관련 입력

완료 기준:

- [ ] 핵심 흐름 변경 시 회귀를 빠르게 감지할 수 있다.

### 6.2 DB migration smoke test

- [ ] default world schema 생성 테스트
- [ ] 기존 DB에 migration 재실행 테스트
- [ ] 누락 table이 있을 때 ALTER 실패 무시 동작 확인
- [ ] `HAS_STATE` migration 테스트
- [ ] NeedsState 자동 생성 테스트

완료 기준:

- [ ] 새로 만든 월드와 기존 월드 모두 실행 가능하다.

### 6.3 Prompt snapshot regression test

- [ ] fixed prompt snapshot
- [ ] genre prompt snapshot
- [ ] dynamic prompt snapshot
- [ ] scene type별 prompt snapshot
- [ ] memory 포함/미포함 snapshot
- [ ] secondary NPC 포함 snapshot
- [ ] secret public hint 포함 snapshot

완료 기준:

- [ ] 의도하지 않은 prompt 구조 변경을 감지할 수 있다.

## Priority 7 - 장기 개선 과제

### 7.1 SceneState 저장소 재검토

- [ ] 현재 in-memory 유지로 충분한지 판단
- [ ] session별 scene state 저장 필요성 검토
- [ ] reroll/edit과의 관계 정리
- [ ] DB 저장 시 schema 초안 작성

완료 기준:

- [ ] 앱 재시작 후 장면 연속성이 필요한지 여부가 결정된다.

### 7.2 Multi-world 확장 규칙 정리

- [ ] world template checklist 작성
- [ ] 새 world 추가 시 필수 구현 method 정리
- [ ] world-specific scene mapping 규칙 작성
- [ ] world-specific props schema 작성법 정리
- [ ] world-specific scheduler 확장 규칙 정리

완료 기준:

- [ ] 새 월드를 추가할 때 기존 엔진을 수정하지 않고 확장할 수 있다.

### 7.3 시스템별 enable/disable config 추가

- [ ] config에 system toggle 추가
  - [ ] memory
  - [ ] needs
  - [ ] social
  - [ ] reputation
  - [ ] personality_drift
  - [ ] goals
  - [ ] items
  - [ ] secrets
  - [ ] organic
  - [ ] static_events
- [ ] system disabled 시 prompt/context/update 영향 정리
- [ ] debug UI 또는 log에 활성 시스템 표시

완료 기준:

- [ ] 특정 시스템 문제를 독립적으로 디버깅할 수 있다.
- [ ] 월드별로 필요 없는 시스템을 끌 수 있다.

## Suggested Execution Order

### Phase 1 - 불일치 제거

- [ ] 0.1 OOC 시간 처리 검증 및 안정화
- [x] 0.2 `HAS_STATE` / `HAS_DYNAMIC_STATE` 정리
- [x] 0.3 프로젝트 문서와 실제 경로 동기화

### Phase 2 - 내부 표준화

- [ ] 1.1 Scene type canonicalization 추가
- [ ] 1.2 State Mutation Ownership 문서 작성
- [ ] 1.3 State mutation logging 최소 골격 추가

### Phase 3 - Actor / Prompt 안정화

- [ ] 2.1 Actor streaming / non-streaming 공통화
- [ ] 2.2 `<analyze>` block fallback 처리
- [ ] 2.3 PromptBuilder debug snapshot 개선

### Phase 4 - Context 품질 개선

- [ ] 3.1 Memory recall reason 추가
- [ ] 3.2 ContextPlanner rule 정리
- [ ] 3.3 Generic prompt node priority 정리

### Phase 5 - Postprocess 리팩터링

- [ ] 4.1 `process_actor_response()` handler 분리
- [ ] 4.2 MutationPlan 도입 검토

### Phase 6 - Schema 안정화

- [ ] 5.1 `props` JSON schema version 도입
- [ ] 5.2 공통 필드 column화 검토

### Phase 7 - 회귀 방지와 장기 확장

- [ ] 6.1 핵심 턴 플로우 테스트 작성
- [ ] 6.2 DB migration smoke test
- [ ] 6.3 Prompt snapshot regression test
- [ ] 7.1 SceneState 저장소 재검토
- [ ] 7.2 Multi-world 확장 규칙 정리
- [ ] 7.3 시스템별 enable/disable config 추가

## 지금 바로 시작할 추천 작업

```text
1. OOC 시간 처리 검증 및 안정화
2. 프로젝트 문서와 실제 경로 동기화
3. Scene type canonicalization 추가
4. State Mutation Ownership 문서 작성
```

이 네 가지는 새 기능을 추가하기 전에 구조적 혼선을 줄이는 작업이다. 특히 scene type과 state ownership은 뒤로 미룰수록 수정 범위가 커질 가능성이 높다.
