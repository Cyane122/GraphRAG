# 장기기억 안정화 v1 계획

## Summary

기존 `Event -> CharacterMemory -> Recall` 구조는 유지하고, 먼저 기억 오염과 recall 불안정성을 줄인다. `ArcMemory`와 scene 단위 `EpisodeLog`는 이번 v1에서 구현하지 않고, 나중에 붙일 수 있도록 source/metadata 기반만 준비한다.

핵심 원칙은 다음과 같다.

- `LLM Extractor`는 후보와 신호를 뽑는다.
- `Deterministic Gate`가 최종 `reject/create/reinforce/update/resolve`를 결정한다.
- `state_summary`는 객관 앵커, `summary`는 생성 시점 주관 기억, `narrative_summary`는 prompt/왜곡/압축용 현재 회상 문장으로 고정한다.
- RecallComposer는 LLM 호출 없이 pinned/recent/vector/type 슬롯을 조합한다.

## Key Changes

### 1. Memory Gate

- `turn_extractor` 출력에 `signals`, `source_type`, `suggested_memory_type`, `evidence_quote`를 명시한다.
- `ensure_memories_for_event()` 앞단에 deterministic gate를 둔다.
- Gate 결과는 `reject`, `create`, `reinforce`, `update`, `resolve` 중 하나로 한다.
- Routine hard reject:
  - 단순 인사, 한 단어 대답, 의미 없는 잡담, 단순 이동, 결과 없는 식사/수업/휴식, 감정/관계/상태 변화 없는 정보 교환.
- Memory 생성 기준:
  - `importance < 3` + strong signal 없음: reject
  - `importance 3~4` + signal 없음: reject
  - `importance 3~4` + signal 있음: create 가능
  - `importance >= 5`: create 가능, 중복이면 reinforce/update 우선
- Strong signals:
  - `promise`, `appointment`, `secret`, `first_time`, `misunderstanding`, `conflict`, `reconciliation`, `betrayal`, `boundary`, `gift`, `item_anchor`, `debt`, `favor`, `identity`, `emotional_wound`
- `gossip`은 조건부 strong signal:
  - named source가 있거나, 관계 평가/행동 결정/secret/conflict/trust delta와 연결될 때만 저장한다.

### 2. Memory Metadata

- `Memory`에 다음 컬럼을 추가하는 migration을 설계한다.
  - `status STRING DEFAULT 'active'`
  - `source_commit_id STRING DEFAULT ''`
  - `source_type STRING DEFAULT 'direct_experience'`
  - `confidence DOUBLE DEFAULT 0.75`
  - `signals STRING DEFAULT '[]'`
  - `salience DOUBLE DEFAULT 0.0`
  - `recall_count INT64 DEFAULT 0`
  - `last_recalled_at STRING DEFAULT ''`
  - `reinforced_count INT64 DEFAULT 0`
  - `last_reinforced_at STRING DEFAULT ''`
  - `resolved_at STRING DEFAULT ''`
- 기존 `event_id`는 v1에서 `source_event_id` 역할로 사용한다. 별도 `source_event_id` 컬럼은 추가하지 않는다.
- `Event`에는 `source_commit_id STRING DEFAULT ''`, `status`는 기존 필드를 활용한다.
- `status` 값:
  - Memory: `active`, `resolved`, `corrected`, `invalidated`
  - Event: 기존 `active/closed` 유지, committed history 무효화가 필요할 때만 `invalidated` 추가 허용

### 3. Type-Specific Memory Format

- 우선 타입은 다음 5개를 명확히 처리한다.
  - `promise`: 누가 무엇을 언제/어떻게 약속했는지, 미해결 여부 유지
  - `misunderstanding`: 캐릭터 해석과 불확실성을 함께 저장
  - `gossip`: source와 미확인 상태를 문장에 포함
  - `relational`: 관계 해석 변화 또는 반복 패턴 후보만 저장
  - `item`: 물건, 사건, 감정 앵커를 함께 저장
- `state_summary`는 객관 사실만 저장하고 왜곡 금지.
- `summary`는 생성 시점의 주관 기억으로 보존.
- `narrative_summary`만 decay/compression/distortion 대상.
- Prompt label은 confidence 숫자 대신 사용한다.
  - `>= 0.85`: `confirmed`
  - `0.65~0.84`: `likely`
  - `0.45~0.64`: `uncertain`
  - `< 0.45`: `unverified`
  - `gossip`은 기본 `unverified`

### 4. Duplicate / Reinforce

- 새 Memory 생성 전 중복을 검사한다.
  - same `event_id`
  - same `source_commit_id`
  - same owner + same type + similar summary
  - same promise/open thread target
  - same item anchor
- 중복 처리:
  - 완전 동일: reject
  - 반복 패턴: reinforce
  - 기존 오해 해소: resolve/correct
  - 기존 소문 직접 확인: update confidence/source_type
  - 같은 사건의 다른 캐릭터 관점: create 허용
- reinforce는 새 Memory를 만들지 않고 기존 Memory의 `salience`, `reinforced_count`, `last_reinforced_at`, 필요 시 `importance/confidence`만 보수적으로 올린다.

### 5. RecallComposer

- `_recall_relevant_memories()`를 슬롯형 read layer로 정리한다.
- pinned/recent/vector/type-specific 조회를 각각 독립 try로 분리한다.
- embedding/vector 실패는 vector 슬롯만 비운다.
- 기본 슬롯:
  - pinned: 최대 1~2
  - recent: 최소 1
  - vector relevant: 2~3
  - relational/promise/open thread 계열: 0~2
  - item/gossip/misunderstanding은 현재 입력과 관련 있을 때만
- pinned가 전체 memory block을 독점하지 않도록 cap을 둔다.
- recalled memory id는 prompt build 중 DB에 쓰지 않는다. 나중에 telemetry를 붙일 경우 pending metadata에만 저장하고 accepted commit 때 반영한다.

## Implementation Notes

- 주요 변경 위치:
  - Memory 생성/gate: `src/simulation/systems/memory/`
  - Event 생성/source_commit 전달: `src/simulation/state/apply/events.py`
  - Recall 조합/render: `src/agents/manager/core_context.py`, `src/agents/context/renderer.py`
  - Extractor 후보 확장: `src/simulation/state/extract/turn_extractor.py`
- migration은 기존 DB 호환을 위해 `ALTER TABLE ... ADD ... DEFAULT ...` best-effort 방식으로 둔다.
- 기존 `episodic/emotional/relational` 값은 허용하되, 새 타입으로 normalize 가능한 경우만 변환한다.
- `Relationship.summary`는 현재 관계 톤으로 유지한다. `relational Memory`는 관계 톤을 바꿀 만한 근거나 반복 패턴 후보일 때만 만든다.
- `ArcMemory`와 `NarrativeEpisodeLog` 개편은 v1 구현 범위 밖이다. 이번 변경은 나중에 ArcMemory가 오염된 단편 기억을 먹지 않게 하는 기반 작업이다.

## Test Plan

- Gate unit-style scenarios:
  - 단순 인사/잡담/이동/식사만 있는 turn은 Memory reject.
  - 약속, 비밀, 오해, 선물, 경계선, 갈등은 낮은 importance라도 Memory create 가능.
  - 같은 약속 반복은 create가 아니라 reinforce.
  - 오해 해명은 기존 misunderstanding을 resolved/corrected로 처리.
  - gossip은 named source 또는 관계 영향이 없으면 reject.
- Recall scenarios:
  - embedding 실패 시 pinned/recent는 유지.
  - vector DB 실패 시 vector 슬롯만 비움.
  - pinned 기억이 많아도 recent/vector가 최소 슬롯을 확보.
  - invalidated/resolved Memory는 기본 recall에서 제외하거나 resolved label로만 제한 노출.
- Regression checks:
  - `python -m py_compile` 대상 변경 파일.
  - 기존 accepted response commit 흐름에서 deferred commit semantics 유지.
  - Memory decay/distortion이 `state_summary`와 `summary`를 덮어쓰지 않고 `narrative_summary`만 변경하는지 확인.

## Assumptions

- 이번 v1은 구현 범위를 `Memory Gate`, metadata migration, 슬롯형 RecallComposer까지로 제한한다.
- committed 과거 turn edit/delete cascade는 아직 구현하지 않고, `source_commit_id`와 `status`로 나중에 invalidation할 수 있게 준비만 한다.
- confidence 숫자는 LLM이 결정하지 않고 `memory_type + source_type` 룰로 산출한다.
- prompt에는 confidence 숫자 대신 `confirmed/likely/uncertain/unverified` 라벨을 렌더링한다.
