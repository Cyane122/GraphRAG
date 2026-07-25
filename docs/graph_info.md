# Graph Node & Edge Reference
*sunghwa_high_school 세계관 기준 / 최종 수정: 2026-05-28*

---

## 목차

1. [노드 타입 목록](#1-노드-타입-목록)
2. [엣지(관계) 타입 목록](#2-엣지관계-타입-목록)
3. [현재 시각화 상태](#3-현재-시각화-상태)
4. [성화고 세계관 연결 예시](#4-성화고-세계관-연결-예시)
5. [미시각화 노드·엣지 확장 방향](#5-미시각화-노드엣지-확장-방향)

---

## 1. 노드 타입 목록

스키마 정의: `src/assets/worlds/base.py` → `_build_tables()`  
총 **24개** 노드 테이블.

### 1.1 캐릭터 핵심 노드

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **Character** | `id` | `name`, `aliases[]`, `type` (`pc`/`npc`/`narrator`) | 모든 인물의 중심 노드. 다른 노드들은 이 노드에서 방사형으로 연결됨 |
| **StaticProfile** | `id` | `props`(JSON), `age`, `gender`, `role` | 불변 배경 정보. 가족관계·출생지·성격 기반값 등. `props`는 세계별 자유 구조 |
| **DynamicInformation** | `id` | `props`(JSON) | 천천히 변하는 속성(외모·기술·평판·사회적 상황). `props`는 세계별 자유 구조 |
| **Personality** | `id` | `props`(JSON) | 성격·말투·행동 경향. 기억 왜곡 방향의 기준점 |
| **DynamicState** | `id` | `mood`, `stress_level`, `stress`, `location_id`, `outfit`, `emotional_state`, `energy`, `nervousness`, `attachment_risk`, `pregnant`, `cycle_day` 등 | 매 턴 갱신되는 현재 상태. 신체·심리·위치를 모두 포함 |
| **IntimateProfile** | `id` | `props`(JSON) | 친밀 장면 행동 패턴·반응 방식 |
| **WorkplaceProfile** | `id` | `props`(JSON) | 직장/학교 상황 행동 패턴 |
| **DialogueExamples** | `id` | `props`(JSON) | 캐릭터 고유 대사 예시 모음. Actor 퓨샷에 활용 |

### 1.2 세계 상태 노드

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **GlobalState** | `id`=`singleton` | `currentTime`, `currentLocationId`, `weather`, `schedule_slot`, `flags` | 세계 전역 상태 싱글톤. 항상 1개만 존재 |
| **Location** | `id` | `name`, `description`, `atmosphere`, `district`, `summary`, `prompt_hint`, `prompt_priority`, `tags[]` | 장소 노드. `PART_OF`로 계층 구조 표현 |
| **Rule** | `id` | `name`, `prompt_hint`, `prompt_priority`, `tags[]`, `location_id`, `owner_id`, `scene_type`, `status` | 행동 규칙/제약. 특정 장소·캐릭터·장면 타입에 바인딩 |
| **SpeechProfile** | `id` | `char_id`, `audience_id`, `scene_type`, `prompt_hint`, `prompt_priority` | 특정 상대·장면 조합에서의 말투 지침 |
| **RelationshipProfile** | `id` | `source_id`, `target_id`, `scene_type`, `prompt_hint` | 특정 관계의 뉘앙스·분위기 서술. `PROFILE_TARGET`으로 대상 연결 |

### 1.3 기억·이벤트 노드

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **Event** | `id` | `summary`, `timestamp`, `location_id`, `impact`, `importance`, `memory_type`, `narrative_summary`, `state_summary`, `embedding`(float[1024]), `status`, `turn_count` | 발생한 사건 기록. 벡터 인덱스로 유사도 검색 가능 |
| **Memory** | `id` | `event_id`, `char_id`, `summary`, `embedding`(float[1024]), `memory_type`, `narrative_summary`, `distortion_level`, `importance`, `summary_level`, `created_at`, `last_decayed_at` | 캐릭터별 사건 인식. `distortion_level`이 높을수록 성격 방향으로 왜곡됨 (의도된 동작) |
| **StaticEvent** | `id` | `name`, `foreshadow_conditions`, `foreshadow_hint`, `trigger_conditions`, `status` | 조건 기반 이벤트. `dormant` → `foreshadow` → `active` 3단계 |

### 1.4 캐릭터 시스템 노드

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **NeedsState** | `id` | `hunger`, `rest`, `social`, `fun`, `safety`, `libido` (모두 DOUBLE) | 욕구 6종 수치 (0.0~1.0). 턴마다 자동 감소, 임계치 초과 시 자율 행동 트리거 |
| **Goal** | `id` | `owner_id`, `title`, `description`, `status`, `progress`, `subtlety`, `next_hint`, `trigger_conditions`, `completion_conditions`, `last_progressed_at` | 캐릭터 단기·장기 목표. `progress` 0~100 |
| **Secret** | `id` | `owner_id`, `title`, `private_summary`, `public_hint`, `status`, `sensitivity`, `reveal_conditions`, `current_reveal_level`, `last_hinted_at` | 캐릭터 비밀. 복선 힌트와 실제 내용 분리 저장 |
| **Schedule** | `id` | `owner_id`, `name`, `activity`, `recurrence`, `day_of_week`, `day_of_weeks[]`, `start_time`, `end_time`, `start_minute`, `end_minute`, `location_id`, `status`, `tags[]` | 반복 일정·루틴. `start_minute`/`end_minute`으로 분 단위 비교 |
| **PersonalFact** | `id` | `subject_id`, `audience_id`, `category`, `fact_text`, `normalized_key`, `status`, `confidence`, `source`, `valid_from`, `valid_until`, `created_at`, `updated_at` | 특정 캐릭터가 다른 캐릭터에 대해 아는 사실 |

### 1.5 아이템 노드

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **Item** | `id` | `name`, `description`, `owner_id`, `location_id`, `emotional_weight`, `visibility`, `last_seen_at` | 소지품·물건. `ANCHORS_MEMORY`로 기억과 연결 가능 |

### 1.6 소셜 미디어 노드 (sunghwa_high_school 특화)

| 타입 | Primary Key | 핵심 속성 | 역할 |
|------|-------------|-----------|------|
| **KakaoRoom** | `id` | `name`, `topic`, `status`, `created_at`, `last_active_at` | 카카오톡 방. 1:1 또는 그룹 채팅 |
| **KakaoMessage** | `id` | `room_id`, `sender_id`, `sender_name`, `content`, `timestamp`, `source`, `status` | 카카오톡 메시지 단위 |

---

## 2. 엣지(관계) 타입 목록

스키마 정의: `src/assets/worlds/base.py` → `_build_tables()` rel_tables  
총 **35개** 관계 테이블.

### 2.1 Character → 정적 프로파일 (단방향)

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **HAS_PROFILE** | Character | StaticProfile | - | 불변 배경 정보 연결 |
| **HAS_INFO** | Character | DynamicInformation | - | 천천히 변하는 속성 연결 |
| **HAS_PERSONALITY** | Character | Personality | - | 성격·말투 프로파일 연결 |
| **HAS_STATE** | Character | DynamicState | - | 현재 상태 연결 (1:1) |
| **HAS_INTIMATE** | Character | IntimateProfile | - | 친밀 장면 프로파일 연결 |
| **HAS_WORKPLACE** | Character | WorkplaceProfile | - | 직장/학교 프로파일 연결 |
| **HAS_DIALOGUE_EXAMPLES** | Character | DialogueExamples | - | 대사 예시 연결 |

### 2.2 Character → 위치·규칙

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **LOCATED_AT** | Character | Location | - | 현재 위치. `DynamicState.location_id`와 동기화 |
| **HAS_SPEECH_PROFILE** | Character | SpeechProfile | - | 말투 지침 연결 (여러 개 가능, scene_type별) |
| **HAS_RELATIONSHIP_PROFILE** | Character | RelationshipProfile | - | 관계 뉘앙스 프로파일 연결 |
| **PROFILE_TARGET** | RelationshipProfile | Character | - | 프로파일이 기술하는 대상 캐릭터 |
| **APPLIES_AT** | Rule | Location | - | 규칙이 적용되는 장소 |
| **RULE_FOR_CHARACTER** | Rule | Character | - | 규칙이 적용되는 캐릭터 |

### 2.3 Character ↔ Character

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **RELATIONSHIP** | Character | Character | `type`, `affinity`(INT), `trust`(INT), `duration`, `origin`, `current_status`, `summary`, `shared_events[]`, `last_interaction` | 양방향으로 각각 존재 (A→B, B→A). `affinity`/`trust` -100~100 |

### 2.4 Character → 기억·이벤트

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **INVOLVED_IN** | Character | Event | - | 이 사건에 참여했음 |
| **OCCURRED_AT** | Event | Location | - | 이 사건이 발생한 장소 |
| **REMEMBERS** | Character | Memory | - | 캐릭터가 이 기억을 가짐 |
| **OF_EVENT** | Memory | Event | - | 이 기억이 어느 사건에서 비롯됐는지 |

### 2.5 Character → 시스템 (욕구·목표·비밀·일정)

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **HAS_NEEDS** | Character | NeedsState | - | 욕구 상태 연결 (1:1) |
| **PURSUES** | Character | Goal | - | 캐릭터가 추구하는 목표 |
| **GOAL_RELATED_EVENT** | Goal | Event | - | 목표 진행에 관련된 사건 |
| **HAS_SECRET** | Character | Secret | - | 캐릭터의 비밀 |
| **ROOTED_IN** | Secret | Event | - | 비밀이 뿌리내린 사건 |
| **TRIGGERED_BY** | Secret | Item | - | 비밀을 연상시키는 물건 |
| **HAS_SCHEDULE** | Character | Schedule | - | 캐릭터의 일정 |
| **SCHEDULED_AT** | Schedule | Location | - | 일정이 진행될 장소 |

### 2.6 Character → 아이템

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **OWNS** | Character | Item | - | 현재 소유자 |
| **GAVE** | Character | Item | - | 선물한 이력 (소유 이전 후에도 남음) |
| **ANCHORS_MEMORY** | Item | Memory | - | 물건이 특정 기억을 고정·상기시킴 |

### 2.7 위치 계층

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **PART_OF** | Location | Location | - | 하위 장소 → 상위 장소. 재귀적으로 최대 4단계 탐색 |

### 2.8 PersonalFact

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **KNOWS_FACT** | Character | PersonalFact | - | 청중(audience) 캐릭터가 이 사실을 알고 있음 |

### 2.9 StaticEvent

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **EVENT_INVOLVES** | StaticEvent | Character | - | 이벤트가 영향을 주는 캐릭터 |

### 2.10 소셜 미디어

| 엣지 | FROM | TO | 속성 | 설명 |
|------|------|----|------|------|
| **MEMBER_OF** | Character | KakaoRoom | - | 방 참여 멤버 |
| **ROOM_HAS_MESSAGE** | KakaoRoom | KakaoMessage | - | 방에 속한 메시지 |
| **SENT_KAKAO** | Character | KakaoMessage | - | 메시지 발신자 |

---

## 3. 현재 시각화 상태

**뷰어 파일:** `public/graph/ppt_viewer.html`  
**데이터 소스 (우선순위 순):**
1. 정적 export: `public/graph/export/graph_export.js` (`export_graph_json.py`로 생성)
2. 라이브 서버: `http://127.0.0.1:8765` (`graph_server.py` → `/api/load`, `/api/threads`)  
   — 2초마다 자동 폴링, 15초마다 스레드 목록 갱신

**데이터 모델:** `src/ui/graph_models.py` (`GraphNode`, `GraphEdge`, `GraphSnapshot`)  
**빌더:** `src/ui/graph_loader.py` → `_build_snapshot()`

---

### 3.1 렌더링되는 노드 타입과 색상

| `node.type` | 원본 DB 타입 | `node.id` 패턴 | 색상 | 뷰어 표시명 | subtitle 기준 |
|-------------|-------------|----------------|------|-------------|--------------|
| `character` | Character | `character:{id}` | `#a78bfa` violet | Character | `type` (pc/npc) |
| `location` | Location | `location:{id}` | `#34d399` emerald | Location | `id` |
| `event` | Event | `event:{id}` | `#fbbf24` amber | Event | `timestamp` / `status` |
| `memory` | Memory | `memory:{id}` | `#e879f9` pink | Memory | `created_at` / `memory_type` |
| `state` | DynamicState | `state:{char_id}` | `#60a5fa` sky | State | `physical_condition` / `mood` |
| `global` | GlobalState | `global:singleton` | `#94a3b8` slate | Global | `currentTime` |
| `dynamic_information` | DynamicInformation | `info:{char_id}` | `#818cf8` indigo | Info | (없음) |
| `static_information` | StaticProfile | `static:{char_id}` | `#fb7185` rose | Profile | `role` / `grade_class` |
| `personal_fact` | PersonalFact | `personal_fact:{id}` | `#2dd4bf` teal | Fact | `category / status` |
| *(기타)* | — | — | `#fbbf24` (기본) | 타입명 | — |

**렌더링 범위 제한:**
- Location: 캐릭터 `LOCATED_AT` 장소 + `GlobalState.currentLocationId`만 포함
- Event: 최근 20개 (`ORDER BY timestamp DESC LIMIT 20`) + INVOLVED_IN 체인
- Memory: 최근 40개 (`ORDER BY created_at DESC LIMIT 40`)
- PersonalFact: 최근 40개 (`ORDER BY updated_at DESC LIMIT 40`)

---

### 3.2 렌더링되는 엣지 타입

| `edge.label` | 방향 | 설명 |
|--------------|------|------|
| `LOCATED_AT` | character → location | 현재 위치 |
| `HAS_STATE` | character → state | DynamicState 연결 |
| `HAS_PROFILE` | character → static_information | StaticProfile 연결 |
| `HAS_INFO` | character → dynamic_information | DynamicInformation 연결 |
| `current` | global:singleton → location | GlobalState 현재 장소 |
| `REL / aff {n} / trust {n}` | character → character | RELATIONSHIP (affinity, trust 수치 표시) |
| `INVOLVED_IN` | character → event | 사건 참여 |
| `REMEMBERS` | character → memory | 기억 소유 |
| `OF_EVENT` | memory → event | 기억의 원본 사건 |
| `KNOWS_FACT` | character → personal_fact | 사실 인지 |
| `ABOUT` | personal_fact → character | 사실의 주제 캐릭터 (빌더에서 합성) |

---

### 3.3 그래프 레이아웃

**에고 중심(ego-centric) 방사형 레이아웃** (`computeLayout()` 함수):

```
          [2-hop 노드들] (r=16, 점선 엣지, 최대 18개)
       [1-hop 노드들] (r=28, 실선 엣지)
    [중심 노드] (r=50, 글로우 링)
```

- **중심(hop=0)**: 반경 50px, 외부 글로우 링, 이름 전체 표시
- **1-hop(hop=1)**: 반경 28px, R1=min(168, canvas*0.25) 거리에 배치
- **2-hop(hop=2)**: 반경 16px, R2=min(305, canvas*0.44) 거리에 배치, 파선 엣지
- 노드 클릭 1회 → 중심 이동, 2회 → 상세 패널 오픈
- 우클릭/ESC → 이전 중심으로 돌아가기
- 드래그로 캔버스 패닝 가능

---

### 3.4 캐릭터 상세 패널 탭 구성

Character 노드를 클릭하면 오른쪽 상세 패널이 열리며 6개 탭이 나타남:

| 탭 | 내용 | 데이터 소스 |
|----|------|-------------|
| **Overview** | Character.details + StaticProfile 트레이트 축 + DynamicInformation | `HAS_PROFILE`, `HAS_INFO` 엣지 |
| **State** | DynamicState 모든 필드 | `HAS_STATE` 엣지 |
| **Relations** | affinity/trust 바 차트 + 상태/기간 | `RELATIONSHIP` 엣지 |
| **Events** | 참여 사건 카드 목록 | `INVOLVED_IN` 엣지 |
| **Memories** | 기억 카드 목록 | `REMEMBERS` 엣지 |
| **Facts** | 알고 있는 사실 목록 | `KNOWS_FACT` 엣지 |

**편집 모드**: 라이브 서버 연결 시 "편집" 버튼으로 노드/엣지 속성을 직접 수정 가능  
(정적 export 모드에서는 read-only)

---

### 3.5 트레이트 축 시각화 (StaticProfile.props)

`static_information` 노드의 `props`에 `trait_*` 키가 있으면 **Overview 탭**에 양극형 바 차트로 표시됨. 값 범위: -1.0 ~ 1.0

| props 키 | 좌측 (음수) | 우측 (양수) |
|----------|-------------|-------------|
| `trait_direction_of_energy` | 내향적 | 외향적 |
| `trait_recognition` | 직관적 | 감각적 |
| `trait_judgement` | 감정적 | 이성적 |
| `trait_life_pattern` | 즉흥적 | 계획적 |
| `trait_achievement_orientation` | 안정지향 | 성취지향 |
| `trait_emotional_reactivity` | 둔감함 | 예민함 |
| `trait_attachment_orientation` | 독립적 | 의존적 |
| `trait_social_attention` | 비노출 | 주목추구 |
| `trait_control_orientation` | 순응적 | 주도적 |
| `trait_moral_orientation` | 현실타협 | 원칙중심 |
| `trait_pleasure_orientation` | 절제 | 쾌락추구 |
| `trait_trust_orientation` | 경계형 | 신뢰형 |
| `trait_vitality` | 비활력형 | 활력형 |
| `trait_self_esteem` | 자존감 낮음 | 자존감 높음 |
| `trait_empathy` | 냉담 | 감정이입 |
| `trait_relational_exclusivity` | 개방적 | 독점욕 |

---

### 3.6 하단 로그 패널

화면 하단 고정 패널 (높이 248px), 3개 탭:

| 탭 | 내용 |
|----|------|
| **Events** | 전체 또는 선택 캐릭터의 이벤트 목록 (최신순) |
| **Relations** | 전체 또는 선택 캐릭터의 RELATIONSHIP 엣지 목록 |
| **Facts** | 전체 또는 선택 캐릭터의 PersonalFact 목록 |

---

### 3.7 미렌더링 노드·엣지

다음 타입은 DB에 존재하지만 현재 뷰어에서 표시되지 않음:

**미렌더링 노드 (15개):**
`Personality`, `IntimateProfile`, `WorkplaceProfile`, `DialogueExamples`,
`Rule`, `SpeechProfile`, `RelationshipProfile`,
`NeedsState`, `Goal`, `Secret`, `Schedule`, `StaticEvent`,
`Item`, `KakaoRoom`, `KakaoMessage`

**미렌더링 엣지 (26개):**
`HAS_PERSONALITY`, `HAS_INTIMATE`, `HAS_WORKPLACE`, `HAS_DIALOGUE_EXAMPLES`,
`HAS_SPEECH_PROFILE`, `HAS_RELATIONSHIP_PROFILE`, `PROFILE_TARGET`,
`APPLIES_AT`, `RULE_FOR_CHARACTER`,
`OCCURRED_AT`, `HAS_NEEDS`,
`EVENT_INVOLVES`, `PURSUES`, `GOAL_RELATED_EVENT`,
`OWNS`, `GAVE`, `ANCHORS_MEMORY`,
`HAS_SECRET`, `ROOTED_IN`, `TRIGGERED_BY`,
`HAS_SCHEDULE`, `SCHEDULED_AT`,
`PART_OF`,
`MEMBER_OF`, `ROOM_HAS_MESSAGE`, `SENT_KAKAO`

---

## 4. 성화고 세계관 연결 예시

### 4.1 단일 캐릭터 서브그래프 (백채원 기준)

```
Character(baek_chaewon) "백채원"
  │
  ├─ HAS_PROFILE      → StaticProfile       { 2학년, 배구부, 가족 배경 }
  ├─ HAS_INFO         → DynamicInformation  { 키, 외모, 기술, 평판 }
  ├─ HAS_PERSONALITY  → Personality         { 표면적 무관심, 내면 유혹 경향 }
  ├─ HAS_STATE        → DynamicState        { mood, stress, location_id, outfit }
  ├─ HAS_INTIMATE     → IntimateProfile     { 반응 패턴 }
  ├─ HAS_NEEDS        → NeedsState          { hunger:0.3, rest:0.5, libido:0.6 }
  ├─ HAS_SECRET       → Secret              { 유혹 성향, sensitivity:8 }
  ├─ PURSUES          → Goal                { "에이스 되기", progress:20 }
  ├─ HAS_SCHEDULE     → Schedule            { 배구 연습, 월수금 17:12 }
  ├─ LOCATED_AT       → Location(sunghwa_high_school_gym) "체육관"
  ├─ INVOLVED_IN      → Event(evt_001)      { "첫 스파이크 성공", importance:6 }
  ├─ REMEMBERS        → Memory(mem_001)     { summary, distortion_level:0.1 }
  └─ RELATIONSHIP     → Character(baek_yoojin)  { affinity:30, trust:45 }
```

### 4.2 캐릭터 간 관계망 (volleyball_team 시나리오)

```
park_sian (PC)
  ├─ RELATIONSHIP(affinity:70, trust:60) ──→ baek_yoojin (주장)
  ├─ RELATIONSHIP(affinity:50, trust:40) ──→ kang_dahye (부주장)
  ├─ RELATIONSHIP(affinity:35, trust:30) ──→ baek_chaewon
  ├─ RELATIONSHIP(affinity:20, trust:25) ──→ cha_yerin (1학년)
  └─ RELATIONSHIP(affinity:55, trust:50) ──→ moon_seoyun

baek_yoojin (주장)
  ├─ RELATIONSHIP ──→ kang_dahye   { type:"teammates", affinity:80 }
  ├─ RELATIONSHIP ──→ baek_chaewon { type:"senior-junior" }
  └─ RELATIONSHIP ──→ park_sian    { type:"player-observer" }
```

### 4.3 사건·기억 체인

```
Event(evt_배구_연습_001)
  ├─ OCCURRED_AT → Location(sunghwa_high_school_gym)
  ├─ ← INVOLVED_IN ← Character(park_sian)
  ├─ ← INVOLVED_IN ← Character(baek_yoojin)
  └─ ← OF_EVENT ← Memory(mem_sian_001)   { distortion_level:0.0 }
                ← OF_EVENT ← Memory(mem_yoojin_001) { distortion_level:0.2 }

Memory(mem_sian_001)
  └─ ← REMEMBERS ← Character(park_sian)

Memory(mem_yoojin_001)          ← 성격 방향 왜곡 (의도된 동작)
  └─ ← REMEMBERS ← Character(baek_yoojin)
```

### 4.4 위치 계층 (PART_OF)

```
sunghwa_high_school "성화고등학교" (최상위)
  ├─ ← PART_OF ── sunghwa_classroom_1_7   "1학년 7반" (prompt_priority:20)
  ├─ ← PART_OF ── sunghwa_corridor_1f     "1층 복도"
  ├─ ← PART_OF ── sunghwa_high_school_gym "체육관"
  │     ├─ ← PART_OF ── gym_locker_f      "여자 탈의실"
  │     ├─ ← PART_OF ── gym_shower        "샤워실"
  │     └─ ← PART_OF ── gym_storage       "창고"
  ├─ ← PART_OF ── sunghwa_library         "도서관"
  ├─ ← PART_OF ── sunghwa_nurse_office    "보건실"
  └─ ← PART_OF ── sunghwa_rooftop         "옥상"

sunghwa_downtown "성화 번화가"
  ├─ ← PART_OF ── sunghwa_cafe_A          "카페"
  ├─ ← PART_OF ── sunghwa_karaoke         "노래방"
  └─ ← PART_OF ── sunghwa_convenience_store "편의점"
```

### 4.5 PersonalFact 연결

```
Character(kang_dahye) "강다혜"
  └─ KNOWS_FACT → PersonalFact { subject_id:"baek_chaewon",
                                  category:"behavior",
                                  fact_text:"백채원이 몰래 개인 스파이크 연습을 한다",
                                  confidence:0.8,
                                  status:"active" }
                    └─ ABOUT → Character(baek_chaewon)
```

### 4.6 카카오톡 체인 (소셜 미디어 활성화 시)

```
KakaoRoom(room_volleyball_main) "배구부 단톡방"
  ├─ ← MEMBER_OF ← Character(park_sian)
  ├─ ← MEMBER_OF ← Character(baek_yoojin)
  ├─ ← MEMBER_OF ← Character(kang_dahye)
  └─ ROOM_HAS_MESSAGE → KakaoMessage(msg_001)
                          └─ ← SENT_KAKAO ← Character(baek_yoojin)
                              { content:"내일 연습 17:00", timestamp:"..." }
```

---

## 5. 미시각화 노드·엣지 확장 방향

`_build_snapshot()`에 아래 섹션을 추가하면 시각화에 포함됨.

| 추가할 노드 타입 | 추천 렌더 조건 | 핵심 표시 값 |
|-----------------|---------------|-------------|
| **NeedsState** | 항상 (캐릭터당 1개) | `hunger`, `rest`, `libido` 수치 바 형태 |
| **Goal** | status=`active`인 것만 | `title`, `progress` % |
| **Secret** | status=`active`인 것만 | `title`, `sensitivity` (내용은 숨김) |
| **Schedule** | status=`active` + 현재 시간 겹치는 것 | `activity`, `start_time~end_time` |
| **NeedsState** | 항상 | 6종 수치 |
| **Item** | `emotional_weight >= 5`인 것만 | `name`, `owner` |
| **StaticEvent** | status != `done` | `name`, `status` |
| **KakaoRoom** | 소셜 미디어 활성화 시 | `name`, `topic` |
| **Location (전체)** | 토글 옵션으로 | 계층 구조 포함 |

| 추가할 엣지 | 조건 | 비고 |
|-------------|------|------|
| **HAS_NEEDS** | NeedsState 노드 추가 시 | |
| **PURSUES** | Goal 노드 추가 시 | |
| **HAS_SECRET** | Secret 노드 추가 시 | |
| **OCCURRED_AT** | 이미 Event 노드 있음 | location 노드와 연결 |
| **PART_OF** | Location 전체 뷰 시 | 트리 레이아웃 권장 |
| **OWNS** / **GAVE** | Item 노드 추가 시 | |
| **ANCHORS_MEMORY** | Item + Memory 동시 표시 시 | |

---

*스키마 DDL 원본: `src/assets/worlds/base.py:423`*  
*시각화 쿼리 원본: `src/ui/graph_loader.py:224`*  
*모델 정의: `src/ui/graph_models.py`*
