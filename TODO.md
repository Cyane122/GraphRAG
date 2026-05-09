# GraphRAG 롤플레이 시뮬레이션 엔진 TODO

이 문서는 GraphRAG 롤플레이 엔진의 구조 개선 작업을 단계별로 정리한다.

핵심 원칙:

- 한 번에 전체 파이프라인을 갈아엎지 않는다.
- 현재 동작하는 Chainlit RP 흐름을 유지하면서 위험한 부분부터 분리한다.
- Actor 응답이 확정되기 전에 DB가 오염되지 않도록 한다.
- 프롬프트에 모든 정보를 넣지 않고, 필요한 정보만 골라서 Actor가 바로 쓸 수 있는 문장형 힌트로 제공한다.
- 세계관별 특수 기능과 범용 엔진 기능을 섞지 않는다.

현재 주요 구조:

- `app.py`
  - Chainlit 메시지 루프
  - OOC 처리
  - Actor 스트리밍
  - 리롤/수정 UI
  - deferred commit 관리
- `src/agents/manager.py`
  - 씬 분류
  - 시간 계산
  - 시간/위치 DB 업데이트
  - needs 업데이트
  - memory recall
  - world context 수집
  - PromptBuilder 호출
- `src/agents/prompt_factory/builder.py`
  - Fixed / Genre / Dynamic Prompt 조립
- `src/simulation/state/updater.py`
  - Actor 응답 분석
  - DynamicState / Relationship / Event / Memory / Secret / Goal / Item 후처리

현재 가장 큰 문제:

- `manager.py`가 너무 많은 책임을 가진다.
- Actor 응답 전에도 시간/위치/needs 같은 DB write가 발생한다.
- 리롤은 일부 상태만 복원하므로 side effect가 남을 수 있다.
- State Updater가 LLM 결과를 비교적 직접 DB에 반영한다.
- Dynamic Prompt가 구조적으로 커지기 쉽고, DB raw data가 Actor용 문장으로 충분히 정리되지 않는다.

---

## 1차 작업: 턴 안정화

목표: 한 턴에서 무엇을 읽고, 무엇을 쓰고, 언제 확정하는지 명확히 한다.  
1차 작업은 기능 확장보다 DB 오염 방지와 리롤/수정 안정성을 우선한다.

### 1.1 Turn Router 정리

의도:

- 모든 유저 입력을 무조건 Actor 파이프라인으로 보내지 않는다.
- 일반 RP, OOC, 설정 질문, 리롤, 수정, 시스템 명령을 명확히 분기한다.
- `app.py`의 메시지 루프를 읽었을 때 "이 입력은 어떤 경로로 처리되는가"가 바로 보여야 한다.

현재 문제:

- `app.py`의 `on_message`는 OOC를 처리한 뒤에도 마지막에 `_run_generation(...)`을 호출한다.
- 일부 OOC는 세계 상태만 바꾸고 끝나야 할 수 있는데, 지금 구조에서는 RP 응답 생성까지 이어질 가능성이 있다.
- 설정 질문이나 시스템 명령도 Actor에게 넘기면 세계 상태와 대화 로그가 오염될 수 있다.

작업 항목:

- [x] 입력 유형 enum 또는 문자열 상수를 정의한다.
  - `roleplay`
  - `ooc_patch`
  - `lore_qa`
  - `reroll`
  - `edit`
  - `system_command`
  - `empty`
- [x] `route_user_input(user_input, message)` 형태의 작은 라우터를 만든다.
- [x] 라우터는 "무엇을 할지"만 판단하고 DB write나 LLM 호출은 하지 않는다.
- [x] OOC 처리 결과가 RP 생성까지 이어져야 하는 경우와, OOC만 처리하고 끝나는 경우를 구분한다.
- [x] 설정/세계관 질문은 Actor RP 응답이 아니라 별도 QA 응답으로 처리할 수 있게 경로를 남긴다.
- [x] 리롤/수정은 Chainlit action callback과 충돌하지 않도록 기존 UI 흐름을 보존한다.

완료 기준:

- [x] `on_message`에서 입력 유형별 early return이 명확하다.
- [x] 일반 RP가 아닌 입력은 `_run_generation`에 들어가지 않는다.
- [x] OOC-only 명령은 상태 처리 후 Actor 응답을 생성하지 않는다.
- [x] 기존 리롤/수정 버튼 동작이 유지된다.

주의:

- 처음부터 복잡한 Router 클래스를 만들 필요는 없다.
- `app.py` 안에 작은 함수로 시작해도 된다.
- 기존 `is_ooc`, `parse_ooc` 로직을 뒤집지 말고 감싸는 방식이 안전하다.

---

### 1.2 Deferred Commit / State Diff 안정화

의도:

- Actor 응답이 확정되기 전에는 DB write를 하지 않거나, 최소한 되돌릴 수 있는 형태로 보관한다.
- 리롤/수정 시 이전 응답에서 나온 상태 변경 후보가 DB에 남지 않도록 한다.
- 최종적으로는 "이번 턴에서 생긴 상태 변경 후보"와 "실제로 커밋된 변경"을 분리한다.

현재 문제:

- `_run_generation` 안에서 `run_manager`를 호출하면, `manager.py` 내부에서 `apply_time_updates(...)`가 실행된다.
- `run_needs_update(...)`도 Actor 응답 전에 실행되며, NPC needs와 autonomous event에 영향을 줄 수 있다.
- 리롤 시 `_restore_game_time(...)`으로 시간은 일부 복원하지만, needs나 기타 side effect는 완전히 복원된다고 보기 어렵다.
- `_commit_pending(...)`은 Actor 응답 후처리를 다음 턴 시작 시점으로 미루지만, manager 단계에서 이미 발생한 write까지 포함하지는 못한다.

작업 항목:

- [x] "Actor 전 계산"과 "Actor 전 DB write"를 구분한다.
- [x] 시간/위치 변경은 먼저 `time_plan` 또는 `pending_effect`로 계산만 한다.
- [x] Actor 응답이 확정된 뒤 `pending_effect`를 commit한다.
- [x] needs 업데이트는 확정된 시간 경과를 기준으로 commit 단계에서 실행하도록 옮기는 것을 검토한다.
- [x] autonomous event 생성은 리롤 가능한 pending 단계에서는 실행하지 않는다.
- [x] pending 구조에 manager side effect를 포함한다.

pending 구조 초안:

```json
{
  "turn_id": "turn_001",
  "user_input": "...",
  "ai_response": "...",
  "scene_types": ["daily"],
  "time_plan": {
    "base_time": "2025-01-01T12:00:00",
    "elapsed_minutes": 3,
    "new_location_id": null,
    "new_weather": null
  },
  "pending_effects": [
    {
      "type": "global_time_update",
      "target": "GlobalState:singleton",
      "field": "currentTime",
      "old_value": "2025-01-01T12:00:00",
      "new_value": "2025-01-01T12:03:00"
    }
  ],
  "pending_state_diff": [],
  "committed_diff": [],
  "rejected_diff": []
}
```

완료 기준:

- [x] 리롤 시 Actor 전 side effect가 남지 않는다.
- [x] 수정 시 수정된 응답 기준으로 상태 변경 후보를 다시 계산할 수 있다.
- [x] `pending_commit` 안에 Actor 응답뿐 아니라 시간/위치/시스템 side effect 후보도 들어간다.
- [x] commit 시점이 코드상 한 곳으로 모인다.

주의:

- 이 단계에서 완전한 Branch/Rollback 시스템을 만들지 않는다.
- 우선은 "마지막 pending 턴"의 리롤/수정 안정화만 목표로 한다.
- 기존 DB helper를 대규모로 바꾸기보다, write 호출 위치를 commit 단계로 늦추는 것이 먼저다.

---

### 1.3 State Update Confidence / Rule Guard

의도:

- Actor 응답을 DB에 반영하기 전에 최소한의 검수 단계를 둔다.
- LLM이 애매하게 추출한 상태 변경을 바로 커밋하지 않는다.
- 비유적 표현, PC 조작, 시점 위반, 설정 충돌이 DB에 들어가는 것을 막는다.

현재 문제:

- `src/simulation/state/updater.py`의 `_run_combined_update(...)`는 상태 변경, 호감도, 이벤트 생성을 한 번에 판단한다.
- 반환된 `dynamic_state`, `relationship_delta`, `new_event`는 confidence 없이 적용된다.
- `_needs_classification(...)` 게이트는 있지만, 업데이트 후보의 근거와 신뢰도는 저장하지 않는다.
- Output Guard가 없어서 문제가 있는 Actor 응답도 State Updater까지 갈 수 있다.

작업 항목:

- [x] Actor 응답에 대한 rule-based guard 함수를 만든다.
- [x] 최소 검사 항목을 정의한다.
  - PC 대사/행동/감정 생성
  - 시점 위반
  - 금지 표현
  - private secret 과노출
  - 명백한 설정 충돌
- [x] Guard 결과 구조를 정의한다.

Guard 결과 예시:

```json
{
  "passed": true,
  "issues": [],
  "severity": "none"
}
```

- [x] State Update 후보마다 `confidence`, `evidence`, `commit_policy`를 붙이는 구조를 설계한다.
- [x] confidence가 낮거나 evidence가 없는 변경은 `hold` 처리한다.
- [x] 비유 표현은 물리 상태 변경으로 커밋하지 않는다.
- [x] 감정 표현은 위치 이동, 부상, 질병 등으로 오해하지 않는다.

State diff 후보 예시:

```json
{
  "target": "Character:eun_seo/DynamicState",
  "field": "mental_condition",
  "old_value": "stable",
  "new_value": "anxious",
  "confidence": 0.72,
  "evidence": "은서가 대답을 미루며 손끝을 계속 만지작거렸다.",
  "commit_policy": "deferred"
}
```

정책 초안:

| Confidence | 처리 |
|---:|---|
| 0.85 이상 | commit 가능 |
| 0.60 ~ 0.85 | deferred 또는 hold |
| 0.60 미만 | hold |
| 모순 감지 | reject |

완료 기준:

- [x] Guard 실패 응답은 State Updater로 넘어가지 않는다.
- [x] State Updater 결과에 evidence를 남길 수 있다.
- [x] 애매한 변경은 DB write 없이 pending/hold로 남는다.
- [x] 기존 이벤트 생성과 메모리 생성 흐름은 깨지지 않는다.

주의:

- 처음부터 LLM Critic을 붙이지 않는다.
- rule-based guard를 먼저 만들고, 정말 애매한 경우만 나중에 LLM Critic 후보로 넘긴다.
- Guard는 산문 품질 평가기가 아니라 DB 오염 방지 장치다.

---

## 2차 작업: 컨텍스트 파이프라인 정리

목표: 매 턴 모든 시스템을 실행하지 않고, 필요한 컨텍스트만 골라서 짧고 유용한 Actor 힌트로 렌더링한다.  
2차 작업은 Manager 비대화 해소와 Dynamic Prompt 최적화가 중심이다.

### 2.1 Manager 책임 분리

의도:

- `manager.py`를 "모든 것을 하는 함수"에서 "턴 준비 오케스트레이터"로 줄인다.
- 각 단계가 무엇을 입력받고 무엇을 반환하는지 명확히 한다.
- 나중에 Context Planner, SceneState, Budgeter를 추가해도 `run_manager`가 계속 비대해지지 않게 한다.

현재 문제:

- `run_manager(...)`가 다음 일을 모두 한다.
  - world 로드
  - global state 조회
  - scene/time 분류
  - 시간 DB 업데이트
  - needs 업데이트
  - memory decay
  - character/relationship/event 조회
  - vector memory recall
  - location 조회
  - present NPC 감지
  - SNS/world context/static event/goal/item/secret 조회
  - PromptBuilder 호출
- 이 구조에서는 특정 시스템을 조건부로 끄거나, 리롤 가능한 pending effect로 바꾸기 어렵다.

우선 분리 후보:

- `Scene Classifier`
  - 입력: user_input, recent_story, global_state, allowed_locations, scene_descriptions
  - 출력: scene_types, action_type, elapsed_minutes, new_location_id, new_weather
- `Context Planner`
  - 입력: scene_types, user_input, SceneState, world_config
  - 출력: required_systems, required_nodes, query_focus, budget
- `Simulation Executor`
  - 입력: plan, base_time, current_time 후보
  - 출력: pending_effects, dynamic system hints
- `Context Renderer`
  - 입력: DB records, planner result, budget
  - 출력: Dynamic Prompt에 들어갈 문장형 블록

작업 항목:

- [x] 기존 `run_manager`의 내부 단계를 주석 기준으로 더 명확히 나눈다.
- [x] 먼저 파일 분리 없이 helper 함수로 경계를 만든다.
- [x] 안정화 후 별도 모듈로 이동한다.
- [x] 함수 반환값에 DB write 결과와 prompt context를 섞지 않는다.
- [x] scene/time plan은 DB write 없이 반환할 수 있게 한다.

완료 기준:

- [x] `run_manager`를 읽었을 때 단계별 역할이 명확하다.
- [x] 시간 계획과 시간 커밋이 분리된다.
- [x] Context 조회와 Context 렌더링이 분리된다.
- [x] PromptBuilder는 이미 정리된 context를 받아 조립하는 역할에 가까워진다.

주의:

- 2차 작업에서 대규모 파일 이동을 먼저 하지 않는다.
- 기존 함수명을 최대한 유지하면서 경계를 만든다.
- 테스트 스위트가 없으므로, 동작 경로를 작게 바꾸고 수동 실행으로 확인한다.

---

### 2.2 SceneState / Context Planner 추가

의도:

- 현재 턴의 장면 상태를 별도 구조로 보관한다.
- 이번 턴에 어떤 시스템과 노드가 필요한지 명시적으로 결정한다.
- "매 턴 모든 시스템 실행"을 피한다.

SceneState 최소 스키마:

```json
{
  "scene_id": "scene_001",
  "location": "home",
  "participants": ["player", "char"],
  "scene_type": "daily",
  "mood": "calm",
  "tension": 0.2,
  "physical_distance": "normal",
  "unresolved_beats": [],
  "last_action": ""
}
```

필드 의미:

- `scene_id`: 현재 장면 식별자. 장면 전환 시 새로 만든다.
- `location`: 현재 장면의 대표 장소.
- `participants`: 현재 장면에 실제 등장한 캐릭터.
- `scene_type`: daily, emotional, physical, intimate, workplace, aegyo 등.
- `mood`: 장면의 표면 분위기.
- `tension`: 0.0~1.0 긴장도.
- `physical_distance`: distant / normal / close 등.
- `unresolved_beats`: 아직 회수되지 않은 감정, 대화, 행동.
- `last_action`: 직전 Actor 응답의 마지막 주요 행동.

Context Planner 출력 초안:

```json
{
  "scene_type": "daily",
  "importance": 4,
  "required_systems": ["scene_state", "location", "memory"],
  "required_nodes": ["Location", "RelationshipProfile"],
  "skip_systems": ["reputation", "secrets", "sns"],
  "query_focus": ["current_scene", "relationship", "recent_memory"],
  "budget": {
    "scene": 300,
    "characters": 500,
    "memories": 700
  }
}
```

작업 항목:

- [x] SceneState를 저장할 위치를 정한다.
  - 초기에는 Chainlit session 또는 lightweight DB node 중 하나로 시작한다.
  - 장기적으로는 DB node가 적합하다.
- [x] Actor 응답 후 SceneState 업데이트 후보를 추출한다.
- [x] 장면이 이어지는 경우 `unresolved_beats`를 유지한다.
- [x] 장면이 바뀌는 경우 새 SceneState를 생성한다.
- [x] SceneState를 Context Planner 입력으로 사용한다.
- [x] Planner는 필요한 시스템과 노드만 선택한다.

완료 기준:

- [x] Dynamic Prompt에 last_action, unresolved_beats, mood, distance를 넣을 수 있다.
- [x] Context Planner가 Memory, SNS, Secret, Goals 등을 매 턴 무조건 켜지 않는다.
- [x] 현재 location/participants 기준으로 필요한 context 조회 범위를 줄일 수 있다.

주의:

- SceneState를 처음부터 완벽하게 맞추려 하지 않는다.
- 초기에는 "현재 장면 continuity를 Actor에게 알려주는 힌트"만으로도 충분하다.
- Planner는 LLM 기반보다 rule-based 초안이 먼저 안전하다.

---

### 2.3 Dynamic Context Budgeter / Renderer

의도:

- Dynamic Prompt가 무한히 커지는 것을 막는다.
- DB raw record를 그대로 넣지 않고, Actor가 바로 사용할 수 있는 문장형 힌트로 변환한다.
- 필요한 컨텍스트만 블록별 예산 안에서 삽입한다.

현재 문제:

- `PromptBuilder.build_character_section(...)`는 character 관련 JSON을 그대로 넣는다.
- relationship, events, recall_events, world_context도 각각 다른 형식으로 직접 렌더링된다.
- few-shot과 dynamic context가 함께 커질 경우 토큰 압박이 커진다.
- 어떤 블록이 몇 토큰까지 허용되는지 기준이 없다.

Dynamic Prompt 블록 목표:

- Current Scene
- Active Characters
- Location
- Rules
- Memories
- Relationships
- Goals
- Items
- Subtext
- Summary
- User Input
- Output Checklist

예산 초안:

```json
{
  "scene": 300,
  "characters": 500,
  "location": 250,
  "rules": 250,
  "memories": 700,
  "relationships": 400,
  "goals": 300,
  "items": 250,
  "subtext": 300,
  "recent_summary": 600
}
```

작업 항목:

- [x] 블록별 기본 예산 상수를 만든다.
- [x] 예산 초과 시 자르는 규칙을 정한다.
- [x] 중요도, 최근성, scene relevance 기준으로 항목을 정렬한다.
- [x] Memory / Event / Relationship / Goal / Item / Secret 렌더러를 분리한다.
- [x] 각 렌더러는 JSON dump가 아니라 Actor용 힌트 문장을 반환한다.
- [x] PromptBuilder는 렌더링된 블록을 조립하는 역할에 가깝게 바꾼다.

렌더링 예시:

```text
[Memory]
- 은서는 지난번 다툼을 아직 완전히 넘기지 못했다. 같은 주제가 나오면 대답 전에 손끝을 만지작거릴 가능성이 높다.

[Relationship]
- 은서는 플레이어에게 편하지만, 놀림이 공개적인 상황으로 번지면 선을 긋는다.
```

완료 기준:

- [x] 각 Dynamic Prompt 블록의 최대 크기를 설명할 수 있다.
- [x] DB raw JSON이 Actor Prompt에 그대로 들어가는 비율이 줄어든다.
- [x] 현재 장면과 관련 없는 SNS/Secret/Goal/Memory가 기본 삽입되지 않는다.
- [x] Fixed Prompt에는 동적 정보가 들어가지 않는다.

주의:

- 정확한 tokenizer 계산은 나중에 붙여도 된다.
- 초기에는 문자 수 기반 budget도 허용한다.
- 예산 시스템은 출력 품질을 망치지 않는 선에서 점진적으로 적용한다.

---

## 3차 작업: 범용 노드와 장기 시뮬레이션 확장

목표: 프롬프트에 모든 정보를 고정으로 넣지 않고, 필요한 범용 노드만 조회해서 Actor 힌트로 제공한다.  
3차 작업은 1차/2차 안정화 이후에 진행한다.

### 3.1 최소 범용 노드 추가

의도:

- 세계관별 캐릭터 본문에 흩어진 정보를 범용 노드로 분리한다.
- 현재 장면에 필요한 노드만 조회한다.
- Actor에게는 노드 원문이 아니라 `prompt_hint`를 제공한다.

우선 추가할 노드:

- `Location`
  - 현재 장소, 분위기, 가능한 행동, 장소 규칙.
  - 이미 기본 Location은 있으므로 필드 정리부터 시작한다.
- `Rule`
  - 현재 장면에 적용되는 규칙.
  - 예: 특정 장소 금기, 세계관 법칙, 관계상 넘으면 안 되는 선.
- `SpeechProfile`
  - 캐릭터 말투를 StaticProfile/Personality 본문에서 분리.
  - 캐릭터별/상대별 말투 차이를 표현할 수 있어야 한다.
- `RelationshipProfile`
  - 관계의 질감, 경계선, 갈등 방식, 애정 표현 방식.
  - 단순 affinity 숫자와 별개로 Actor가 사용할 관계 힌트를 제공한다.

공통 필드 초안:

```json
{
  "id": "string",
  "name": "string",
  "summary": "string",
  "prompt_hint": "string",
  "prompt_priority": 0,
  "tags": []
}
```

작업 항목:

- [ ] 기본 노드 공통 필드를 확정한다.
- [ ] `Location`의 기존 필드를 `prompt_hint` 중심으로 정리한다.
- [ ] `Rule` 노드와 관계를 최소 스키마로 추가한다.
- [ ] `SpeechProfile`은 먼저 main NPC 기준으로만 도입한다.
- [ ] `RelationshipProfile`은 main NPC ↔ PC 관계부터 도입한다.
- [ ] 각 노드별 retrieval 규칙을 만든다.

Retrieval 규칙 초안:

- 현재 `SceneState.location`과 연결된 `Location`만 기본 조회한다.
- 현재 장면에 적용되는 `Rule`만 조회한다.
- 현재 발화 캐릭터와 상대에 맞는 `SpeechProfile`만 조회한다.
- 현재 장면에 함께 등장한 캐릭터 간 `RelationshipProfile`만 조회한다.
- 조회된 노드는 원문이 아니라 `prompt_hint` 중심으로 렌더링한다.

완료 기준:

- [ ] 최소 노드가 PromptBuilder에 직접 붙지 않고 renderer를 통해 들어간다.
- [ ] Fixed Prompt에서 캐릭터/관계/장소 정보 일부를 Dynamic 조회로 옮길 수 있다.
- [ ] 새 world를 만들 때 최소 범용 노드 구조를 재사용할 수 있다.

주의:

- 모든 world에 강제로 많은 노드를 요구하지 않는다.
- 없는 노드는 조용히 skip한다.
- 스키마만 추가하고 renderer가 없으면 효과가 없으므로, 노드와 renderer를 함께 작업한다.

---

### 3.2 후순위 범용 노드 검토

의도:

- 좋아 보이는 노드를 전부 기본 스키마에 넣지 않는다.
- 실제 world에서 필요성이 확인된 것만 확장한다.

후순위 후보:

- `Association`
  - 조직, 학과, 가문, 회사, 파벌 등 소속 정보.
  - 학원물/직장물/정치물에서는 유용하지만 모든 world에 필수는 아니다.
- `SceneTemplate`
  - few-shot 대신 짧은 장면 진행 규칙 제공.
  - Dynamic Prompt budget이 정리된 뒤 도입하는 편이 좋다.
- `KnowledgeScope`
  - 캐릭터가 아는 것과 모르는 것 구분.
  - Secret 시스템과 충돌하지 않게 설계해야 한다.
- `SecretReveal`
  - Hidden / Vague Unease / Pattern / Suspicion / Partial Truth / Full Reveal / Aftermath.
  - private_summary를 Actor Prompt에 직접 넣지 않는 규칙이 먼저 필요하다.
- `ReputationProfile`
  - 외부 평판과 실제 모습 분리.
  - SNS/소문 시스템과 연결될 때 가치가 커진다.
- `Routine` / `ScheduleBlock`
  - 캐릭터와 장소의 시간대별 활동 관리.
  - NPC Scheduler와 함께 다뤄야 한다.

작업 항목:

- [x] 각 후보 노드가 필요한 world/use case를 적는다.
- [x] 기본 스키마에 넣을지, world 확장 스키마에 둘지 결정한다.
- [x] `prompt_hint` 없이 raw data만 늘어나는 노드는 보류한다.
- [x] 노드 추가 전 retrieval 조건과 renderer를 먼저 설계한다.

완료 기준:

- [x] 어떤 노드를 지금 하지 않을지 명확하다.
- [x] 기본 스키마가 과도하게 비대해지지 않는다.
- [x] world별 확장 여지를 남긴다.

주의:

- `AmbientContext`, `CultureProfile`, `ActionPattern`, `Arc`는 당장 기본 스키마에 넣지 않는다.
- 이름이 멋진 노드보다 현재 prompt 품질을 실제로 개선하는 노드가 우선이다.

---

### 3.3 장기 시뮬레이션 기능

의도:

- 장기 플레이에서 기억, 요약, SNS, 비밀, NPC 자율성을 자연스럽게 유지한다.
- 단, 1차/2차 안정화 전에는 이 기능들이 DB 오염과 프롬프트 비대화를 악화시킬 수 있으므로 후순위로 둔다.

후보 기능:

- Memory Type 계층화
  - 1차 후보: Episodic / Emotional / Relational
  - 후순위 후보: Sensory / Promise-Debt / Trauma-Scar
- Narrative Summary
  - Actor가 사용할 이야기 흐름 요약.
  - 감정 흐름, 장면 분위기, 미해결 대화를 중심으로 한다.
- State Summary
  - DB 업데이트와 사실 보존을 위한 구조적 요약.
  - 상태 변화 후보와 중요한 사실을 보존한다.
- SNS / Reputation
  - 중요 사건 발생 후 소문 후보 생성.
  - 현재 장면과 관련 있는 피드만 Dynamic Prompt에 삽입.
- Secret / Subtext
  - private 정보를 직접 노출하지 않고 public_hint만 제공.
  - reveal level에 따라 힌트 강도를 조절.
- NPC Scheduler
  - 캐릭터가 항상 PC만 기다리는 느낌을 줄인다.
  - 중요한 사건은 PC가 반응할 여지를 남긴다.

작업 항목:

- [x] Memory Type은 2~3개부터 시작한다.
- [x] 오래된 턴은 Narrative Summary로 압축한다.
- [x] 중요한 사실은 State Summary로 보존한다.
- [x] SNS/Secret/Scheduler는 Context Planner가 필요하다고 판단할 때만 실행한다.
- [x] Branch/Rollback 전체 구현은 마지막 단계로 미룬다.

완료 기준:

- [x] 장기 기능이 매 턴 기본 실행되지 않는다.
- [x] Summary와 Memory가 서로 역할을 침범하지 않는다.
- [x] Secret private 정보가 Actor Prompt에 직접 들어가지 않는다.
- [x] NPC 자율행동이 유저 선택권을 빼앗지 않는다.

주의:

- 장기 시뮬레이션은 "풍부함"보다 "제어 가능성"이 먼저다.
- SNS, Secret, Scheduler는 재미있지만 디버깅 비용이 큰 기능이다.
- Context Planner와 Budgeter가 자리 잡기 전에는 확장하지 않는다.

---

## 당장 하지 않을 것

- [ ] 전체 파이프라인을 한 번에 재작성하지 않는다.
- [ ] 범용 노드를 한 번에 많이 추가하지 않는다.
- [ ] LLM Critic 자동 수정/자동 재생성을 기본 경로에 바로 넣지 않는다.
- [ ] Branch / Rollback 전체 시스템을 초기에 만들지 않는다.
- [ ] `AmbientContext`, `CultureProfile`, `ActionPattern`, `Arc` 같은 고급 노드를 기본 스키마에 먼저 넣지 않는다.
- [ ] PromptCard 캐싱은 Planner/Budgeter/Renderer 이후로 미룬다.
- [ ] world별 특수 규칙을 범용 엔진에 바로 박지 않는다.

---

## 최종 목표 구조

```text
User Input
  -> Turn Router
      -> OOC Patch
      -> Lore QA
      -> Retry / Edit
      -> Roleplay

Roleplay
  -> Scene Classifier
  -> Context Planner
      -> Required Systems 선택
      -> Required Nodes 선택
      -> Context Budget 결정
  -> Simulation Executor
      -> Every Turn Systems
      -> Conditional Systems
      -> Scheduled Systems
  -> Context Renderer
      -> SceneState
      -> Location
      -> Rule
      -> Character Cards
      -> SpeechProfile
      -> RelationshipProfile
      -> Memory
      -> Goal
      -> Item
      -> Secret/Subtext
      -> Summary
  -> PromptBuilder
      -> Global Fixed
      -> World Fixed
      -> Genre / SceneTemplate
      -> Dynamic Context
  -> Actor Streaming
  -> Output Guard / Critic
  -> State Extractor
  -> State Diff / Confidence
  -> Deferred Commit Queue
  -> Turn Snapshot / Logs
```
