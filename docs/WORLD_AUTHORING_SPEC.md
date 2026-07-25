# 세계관 편집 툴 — 핸드오프 스펙 (WORLD_AUTHORING_SPEC)

> **이 문서의 목적**: 세계관(world)을 IDE에서 직접 고치지 않고 **실시간으로 편집하는 별도 툴**을 만들기 위한
> 정보 핸드오프. 툴 빌더(사람/AI)가 "세계관 폴더가 어떻게 구성되고 캐릭터 스키마가 무엇인지"를
> 코드 원문 없이도 정확히 알 수 있도록 정리했다.
>
> 분석 대상: `src/assets/worlds/ts`(최소 구성), `src/assets/worlds/sunghwa_high_school`(대규모 구성).
> 공통 기반: `src/assets/worlds/base.py`, `src/assets/worlds/base_character.py`.

---

## 0. 편집기를 만들기 전에 반드시 알아야 할 3가지 사실

이 3가지가 IDE 편집의 고통의 원인이자, 툴이 해결해야 할 핵심이다.

### (1) 세계관 데이터는 "선언적 파일"이 아니라 "명령형 Python 코드" 안에 있다
캐릭터/위치/관계는 JSON·YAML이 아니라 `build_schema(conn)` 메서드 안의 `conn.execute(...)` / 헬퍼
호출의 **인자**로 들어있다. 즉 데이터와 코드가 섞여 있다.

```python
# 한 캐릭터의 "데이터"는 이렇게 함수 인자로 흩어져 있다
insert_static_inline(conn, self.id, "HAS_PROFILE", "StaticProfile", f"{self.id}_static",
    birth_year=2008, biological_sex="Female", family="...", formative_background="...")
```

→ **편집기 전략 결정이 필요하다** (§12 참조). 가장 깔끔한 길은 "데이터를 JSON/YAML로 분리 +
범용 빌더" 마이그레이션이고, 빠른 MVP는 "빌드된 Kuzu 그래프를 직접 편집"이다.

### (2) 노드 중 일부는 고정 컬럼, 일부는 자유 JSON blob → 이것이 key 드리프트의 근원
- **고정 컬럼 노드**: 스키마(DDL)에 컬럼이 못박혀 있어 key가 안정적. (Item, Goal, Secret, DynamicState …)
- **JSON blob 노드** (`props` 컬럼에 `json.dumps(kwargs)` 통째 저장): **key가 캐릭터마다 자유롭게 다름**.
  `StaticProfile`, `DynamicInformation`, `Personality`, `IntimateProfile`, `WorkplaceProfile`,
  `DialogueExamples` 6종. 헬퍼 `insert_static` / `insert_static_inline`이 만든다.

→ 예: 한 캐릭터는 `biological_sex`, 다른 캐릭터는 `biological_sex_original`/`biological_sex_current`.
즉 **스키마가 강제되지 않는다.** 툴은 이 6종을 "관례 키를 가진 자유 dict"로 다루고,
관례 키 목록(§5)을 자동완성/검증에 써야 한다.

### (3) 모든 상호참조는 검증되지 않는 문자열 id다
`owner_id`, `location_id`, 관계의 target, `reveal_conditions`의 `from`/`to` 전부 그냥 문자열.
오타가 나도 빌드 시점엔 조용히 깨진다(`MATCH`가 0건 매칭). 툴이 **참조 무결성 검사**를 해줘야 한다.

---

## 1. 세계관 폴더 구조

```
src/assets/worlds/{world_id}/
├── __init__.py              # characters/schema를 재노출 (거의 비어 있음)
├── schema.py                # ★ World 서브클래스 + 모듈 끝의 SCENARIOS 리스트 + world_instance alias
├── characters/
│   ├── __init__.py          # 캐릭터 클래스 export
│   ├── {char}.py            # 캐릭터 1명 = 1파일 (build_schema + build_relationship)
│   └── {group}/             # (대규모) 그룹 서브패키지 + roster 주입 헬퍼 (sunghwa: class_1_7, hansung_girls, …)
└── prompt/
    ├── world.md             # 세계 설정 (Fixed 프롬프트)
    ├── prose.md             # 작법 규칙
    ├── cot_append.md        # 월드 공통 CoT/blacklist
    ├── blacklist.md         # (선택)
    ├── scenes/{scene}.md    # 씬 타입별 규칙 (+ {scene}.cot_append.md)
    ├── few_shot/{scene}.md  # 씬별 퓨샷 예시
    ├── characters/{id}.md   # 캐릭터 집중 프롬프트 (+ {id}.cot_append.md)
    └── scenarios/{sid}/
        ├── scenario.md          # 시나리오 스코프/톤/규칙
        ├── opening_scene.md     # 오프닝 내레이션 ({char}/{user} 치환)
        ├── cot_append.md        # (선택) 시나리오 CoT
        └── scenes/{scene}.md    # (선택) 시나리오 전용 씬 오버라이드
```

**규모 스펙트럼** (툴이 둘 다 지원해야 함):

| 항목 | `ts` (최소) | `sunghwa_high_school` (대규모) |
|---|---|---|
| 캐릭터 | 2명, `characters/` 평면 | ~60명, 그룹 서브패키지 + roster 주입 |
| 시나리오 | 1개 (default) | 4개 (default / volleyball_team / sleepy_friend / altered) |
| 시점 | 1인칭 | 3인칭 |
| 스키마 확장 | 없음 (base 그대로) | `_build_tables` 오버라이드로 컬럼/테이블 추가 |
| Goal/Item/Secret/Schedule | 미사용 | 전면 사용 |

---

## 2. 런타임 와이어링 (툴이 "한 세계"를 인식하는 방법)

`schema.py` 모듈 최상단(파일 끝)에 다음이 있어야 한다:

```python
SCENARIOS: list[Scenario] = [ Scenario(scenario_id, display_name, world=<World 인스턴스>, ...), ... ]
world_instance = SCENARIOS[0].world   # schema_builder 하위호환 alias
```

- `Scenario` 데이터클래스(`base.py`): `scenario_id`, `display_name`, `world`(World 인스턴스),
  `default_time`, `default_location_id`, `opening_scene_path`.
- 각 `Scenario`가 Chainlit ChatProfile `"{world_id}/{scenario_id}"`로 노출된다.
- 시나리오별로 **등장 캐릭터 명단**, **시작 시각/위치**, **SCENE_TYPES**, **프롬프트 캐릭터**가 다르다.
  → 같은 World 서브클래스를 `scenario_id`만 바꿔 여러 번 인스턴스화한다(`sunghwa` 패턴).
- 스키마 생성: `python -m src.core.database.schema_builder` → `world.build_schema(conn, scenario_id)`.

**World 서브클래스가 가진 클래스 속성** (세계 정체성 — 툴의 "월드 설정" 화면):

| 속성 | 의미 | 예 |
|---|---|---|
| `WORLD_ID` | 세계 식별자 | `"ts_world"`, `"sung_hwa"` |
| `DEFAULT_PERSPECTIVE` | 기본 시점 | `1`(1인칭) / `3`(3인칭) |
| `SCENE_TYPES` | `{이름: 영문설명}` 씬 타입. classifier LLM에 주입 | `{"daily":"...", "intimate":"..."}` |
| `SCENARIOS` | 시나리오 dict/list | — |
| `SOCIAL_MEDIA` | 카카오/인스타 on·off·강제비활성 | `{"kakao_enabled":False, ...}` |
| `PROMPT_CHARACTER_IDS` | 집중 프롬프트 대상 char_id | `("park_sian",)` |

오버라이드 훅: `get_default_time()`, `get_default_location_id()`, `get_npc_name_map()`(이름/별명→char_id,
**NPC 감지의 핵심**), `get_world_section()`(world.md), `get_specific_prose_rules()`(prose.md),
`get_full_config()`(rating·pov_mode·opening_scene·scenario 조립).

---

## 3. 노드 분류표 — 고정 컬럼 vs 자유 JSON blob

> **편집기에 가장 중요한 표.** 저장/검증 전략이 두 부류에서 완전히 다르다.

### 3-A. 고정 컬럼 노드 (DDL에 컬럼 못박힘 → 안정적 폼 생성 가능)
`Character`, `DynamicState`, `Location`, `Event`, `Memory`, `NeedsState`,
`Item`, `Goal`, `Secret`, `Schedule`, `Rule`, `SpeechProfile`, `RelationshipProfile`,
`GlobalState`, `StaticEvent`, `PersonalFact`, `KakaoRoom`, `KakaoMessage`.
→ 컬럼 목록은 §6(캐릭터), §7(월드)에 전부 명시.

### 3-B. 자유 JSON blob 노드 (`props` STRING 컬럼 = `json.dumps(kwargs)`)
`StaticProfile`, `DynamicInformation`, `Personality`,
`IntimateProfile`, `WorkplaceProfile`, `DialogueExamples`.
→ key가 강제되지 않음. §5의 "관례 키"를 자동완성 후보로만 쓸 것.

### 3-C. 관계(엣지) 테이블
`RELATIONSHIP`(Character→Character, 속성 보유) 외 다수의 무속성 엣지(§10).

모든 DDL의 진실 소스: `src/assets/worlds/base.py`의 `_build_tables()`. 세계는 이를 `super()._build_tables(conn)`
호출 후 `ALTER TABLE ... ADD` / `CREATE NODE TABLE`로 **덧붙이기만** 한다(sunghwa: `name_jp`, `school` 컬럼 추가).

---

## 4. 한 캐릭터 = 어떤 노드 묶음인가 (구조 한눈에)

`Character.build_schema(conn)`이 본체 + 서브노드를 생성한다. 정보를 **변화 속도 3계층**으로 분리하는 게 핵심 설계.

```
Character(id, name, aliases[], type)
│
├─HAS_PROFILE──────→ StaticProfile        [JSON] 불변 배경
├─HAS_INFO─────────→ DynamicInformation   [JSON] 느리게 변함 (외모/성격/스킬/성적정보 …)
├─HAS_PERSONALITY──→ Personality          [JSON] core_traits + speech_style
├─HAS_SPEECH_PROFILE→ SpeechProfile        [고정] 청자/씬별 말투 힌트
├─HAS_STATE────────→ DynamicState         [고정] 자주 변함 (mood/location/outfit/생리 …)
├─OWNS─────────────→ Item                 [고정] 소지품
├─PURSUES──────────→ Goal                 [고정] 목표
├─HAS_SECRET───────→ Secret               [고정] 비밀 (조건부 공개)
├─HAS_SCHEDULE─────→ Schedule             [고정] 루틴
└─RELATIONSHIP─────→ (다른 Character)     [고정+엣지] 방향성 관계 (affinity/trust/설명)
```

서브노드 id 관례: `{char_id}_static`, `{char_id}_info`, `{char_id}_personality`, `{char_id}_state`,
SpeechProfile은 `{char_id}_..._default` 식.

시나리오 분기: `Character.__init__(scenario_id)`가 `DEFAULT_CFG`(전체 기본값) + `SCENARIO_OVERRIDES[sid]`(델타)를
병합해 `self.cfg` 생성. `build_schema`/`build_relationship`에서 `self.cfg`를 읽어 분기.

---

## 5. JSON blob 노드의 관례 키 사전 (강제 X — 자동완성용)

> 아래 key들은 **DDL이 강제하지 않는다.** 두 세계에서 관찰된 관례일 뿐. 툴은 이를 "추천 필드"로 제시하고,
> 캐릭터가 추가한 임의 key도 허용해야 한다. 값은 전부 문자열(자유 서술) 또는 int.

### StaticProfile (불변 배경)
`birth_year`(int), `biological_sex` *또는* `biological_sex_original`/`biological_sex_current`,
`ts_status`, `nationality`, `origin_city`, `family`, `formative_background`.
※ `StaticProfile` 노드는 DDL상 `props` 외에 `age INT`, `gender STRING`, `role STRING` 컬럼도 있으나
실사용은 `props` JSON 중심.

### DynamicInformation (느리게 변하는 특성 — 가장 풍부)
`age`(int), `grade_class`, `height`, `weight`, `measurements`, `appearance`, `body_type`,
`personality`, `speech_style`, `skills`, `current_reputation`, `hobby`,
`sexual_information`, `complex`, `ideal_type`,
(ts 전용) `pre_transition_height` / `pre_transition_weight` / `pre_transition_appearance`.

### Personality
`core_traits`(`+`로 연결한 태그열, 예 `"energetic+jpop_obsessed+impulsive"`), `speech_style`.

### IntimateProfile / WorkplaceProfile / DialogueExamples
두 분석 세계에선 캐릭터별로 미사용(스키마만 존재). 다른 세계에서 쓰일 수 있어 자유 dict로 취급.

---

## 6. 캐릭터 관련 고정 컬럼 노드 — 필드 사전

### 6-A. Character (본체)
| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `id` | STRING | ✔ PK | snake_case 식별자 (예 `han_yuram`) |
| `name` | STRING | ✔ | 표시 이름 (예 `한유람`) |
| `aliases` | STRING[] | | 별명들 (NPC 감지 name_map에 합쳐짐) |
| `type` | STRING | ✔ | `"PC"` / `"npc"` |
| `name_jp`,`school` | STRING | | (sunghwa 전용 ALTER 컬럼) |

### 6-B. DynamicState (자주 변하는 현재 상태) — 와이드 테이블, 부분 집합만 채움
공통 사용 컬럼:
`id`(=`{char}_state`), `mood`, `emotional_state`, `stress_level`(int), `location_id`, `outfit`,
`has_menstrual_cycle`(bool), `cycle_day`(int), `pregnant`(bool), `pregnancy_day`(int),
`cum_shots_this_cycle`(int), `pregnancy_father_id`.
그 외 DDL에 존재하나 **세계 전용**인 컬럼(같은 테이블에 공존): `knee_condition`, `injury_detail`,
`workplace_stress_level`, `physical_condition`, `mental_condition`, `body_perception`, `behavioral_facade`,
`hygiene`/`appearance`/`nervousness`/`social_skill`/`consideration`/`stamina`/`attachment_risk`/`expectation_gap`(DOUBLE),
`physique`, `age_presentation`, `attitude`, `odor`, `penis_size`, `age`(int), `circle_level`(int),
`robe_grade`, `led_color`, `current_task`, `energy`/`stress`(DOUBLE), `injury_marks`.
→ 툴은 "공통 컬럼"을 기본 폼으로, 나머지는 "고급/세계 전용"으로 접어둘 것.

### 6-C. SpeechProfile (말투)
`id`, `name`, `summary`, `prompt_hint`, `prompt_priority`(int), `tags`(STRING[]),
`char_id`(소유자), `audience_id`(대상, 빈문자=공통), `scene_type`(빈문자=공통).

### 6-D. Item (소지품)
`id`, `name`, `description`, `owner_id`, `location_id`, `emotional_weight`(int 0~10),
`visibility`(`"private"`/`"public"`/`"hidden"`), `last_seen_at`(ISO 문자열). 엣지: `OWNS`(소유), `GAVE`(준 사람).

### 6-E. Goal (목표)
`id`, `owner_id`, `title`, `description`, `status`(`"active"`/`"completed"`/`"abandoned"`),
`progress`(int 0~100), `subtlety`(int 0~10, 겉으로 드러나는 정도), `next_hint`(다음 단서),
`trigger_conditions`(조건 JSON, 빈문자 가능), `completion_conditions`(자연어/조건), `last_progressed_at`(ISO). 엣지: `PURSUES`.

### 6-F. Secret (비밀)
`id`, `owner_id`, `title`, `private_summary`(진짜 내용), `public_hint`(겉으로 새는 단서),
`status`(`"hidden"`/`"hinted"`/`"revealed"`), `sensitivity`(int 0~10),
`reveal_conditions`(**조건 JSON**, §8), `current_reveal_level`(int), `last_hinted_at`(ISO). 엣지: `HAS_SECRET`.

### 6-G. Schedule (루틴) — 헬퍼 `insert_schedule(conn, owner_id, schedule_id, **props)`
`id`, `owner_id`, `name`, `activity`, `summary`, `prompt_hint`, `prompt_priority`(int), `material`(JSON 문자열),
`recurrence`(`"weekly"`/`"once"`), `day_of_week`(int)/`day_of_weeks`(int[], **0=월 … 6=일**),
`date`(once용 ISO), `start_time`/`end_time`(`"HH:MM"`), `start_minute`/`end_minute`(int, 시각에서 자동 파생),
`location_id`, `status`, `tags`(STRING[]). 엣지: `HAS_SCHEDULE`, `SCHEDULED_AT`(→Location).

### 6-H. RELATIONSHIP (방향성 관계) ★ affinity/trust 혼란의 핵심
엣지 테이블 컬럼: `type`, `affinity`(INT), `trust`(INT), `duration`, `origin`, `current_status`,
`summary`, `eun_seo_desire`(특정 세계 잔재), `shared_events`(STRING[]), `last_interaction`.

**그러나 캐릭터 파일에서 실제로 쓰는 건 헬퍼 `_insert_rel`이고, 입력은 4-튜플이다:**
```python
"han_yuram": ("best_friend", 0, 88, "중2 때 처음 만나 …(긴 산문 설명)…")
#              type           af tr  → current_status 로 저장됨
```
| 4-튜플 위치 | 컬럼 | 관찰된 의미·범위 |
|---|---|---|
| 1. `rel_type` | `type` | 관계 명칭(자유 문자열): `best_friend`, `close_friend`, `mother`, `rival_captain`, `teammate_setter`, `stranger` … |
| 2. `affinity` | `affinity` | 호감도(INT 0~100). **친한 NPC엔 거의 0으로 시드** → 시뮬레이션이 동적으로 올림. 가족/라이벌엔 실제값(예 74, 55) |
| 3. `trust` | `trust` | 신뢰도(INT 0~100). **실질 시드값은 보통 여기**. stranger=5, 절친=80~88 |
| 4. `status` | `current_status` | **긴 자연어 관계 서술**(변수명이 status라 오해 유발). RELATIONSHIP.`summary` 컬럼은 `_insert_rel`이 안 채움 |

**방향성**: A→B와 B→A는 **별개 엣지**이고 값이 비대칭일 수 있다. (han_yuram→park_sian trust 80,
park_sian→han_yuram trust 88) → 툴은 **양방향을 나란히** 보여줘야 한다(사용자의 "상호 정보 보기" 요구 직결).
`build_relationship(self, other)`이 `{other_id: 4튜플}` dict에서 `other.id`를 찾고, 없으면 no-op(=모르는 사이).

---

## 7. 월드/시나리오 레벨 고정 컬럼 노드

### Location (장소) — `PART_OF` 엣지로 트리 구성
`id`, `name`, `description`, `atmosphere`, `district`, `summary`, `prompt_hint`,
`prompt_priority`(int, 높을수록 우선 렌더), `tags`(STRING[]).
계층: 자식 `-[:PART_OF]->` 부모 (학교 → 층 복도 → 교실).

### Event (사건/기억 원천) — 벡터 인덱스(`embedding`) 보유
`id`, `summary`, `timestamp`(ISO), `location_id`, `impact`, `need_name`, `importance`(int 0~10),
`decay_rate`(DOUBLE, 기억 왜곡 속도/in-game day; 0.05 느림~0.3 빠름), `summary_level`(int 0/1/2),
`memory_type`(`"episodic"`/`"emotional"`/`"relational"`), `narrative_summary`, `state_summary`,
`content`, `status`, `turn_count`, `safety_*`, `embedding`(FLOAT[1024]).
엣지: `INVOLVED_IN`(Character→Event), `OCCURRED_AT`(Event→Location).
`impact` 관찰값: `character_introduction`, `first_meeting`, `group_formation`, `bonding_moment`, `conflict`, `intimate`.

### Rule (규칙) — 헬퍼 `insert_rule`
`id`, `name`, `summary`, `prompt_hint`, `prompt_priority`(int), `tags`(STRING[]),
`location_id`, `owner_id`, `scene_type`, `status`(기본 `"active"`). 엣지: `APPLIES_AT`(→Location), `RULE_FOR_CHARACTER`(→Character).

### StaticEvent (조건부 이벤트 — 복선→발화 2단계) ⚠️ 인프라만 존재, 두 세계 미사용
`id`, `name`, `foreshadow_conditions`(조건 JSON), `foreshadow_hint`, `trigger_conditions`(조건 JSON),
`status`(기본 `"pending"`). 헬퍼 `_merge_static_event`(base_character.py) 존재.
**주의**: ts·sunghwa 모두 실제로 시드하지 않음. 과거사는 일반 `Event`로만 심는다. 툴이 노출하되 "선택 기능"으로.

### GlobalState (싱글톤, id=`"singleton"`)
`currentLocationId`, `currentTime`(ISO), `weather`, `schedule_slot`, `clients_done`/`clients_total`(int),
`flags`(JSON 문자열, 기본 `'{}'`), `today_schedule`, `schedule_date`. → 보통 `build_scenario_data`에서 초기화.

---

## 8. 조건(condition) JSON 공통 포맷

`Secret.reveal_conditions`, `Goal.trigger/completion_conditions`, `StaticEvent.*_conditions`가 공유.
**문자열 안에 든 JSON 배열**(빈 문자열 = 조건 없음):

```json
[{"type": "stat", "field": "trust", "op": ">=", "value": 85, "from": "han_yuram", "to": "park_sian"}]
```
- `type`: 조건 종류 (관찰: `"stat"`).
- `field`: 비교 대상 (`trust`, `affinity` …).
- `op`: `>=`, `>`, `<=`, `<`, `==`.
- `from`/`to`: 관계 방향 (char_id).
→ 툴은 이걸 구조화 폼(드롭다운+숫자)으로 편집하게 만들면 §0-(3) 오타 문제를 크게 줄인다.

---

## 9. enum / 허용값 모음 (검증·드롭다운 후보)

| 대상 | 값 |
|---|---|
| `Character.type` | `PC`, `npc` |
| perspective | `1`(1인칭), `3`(3인칭) |
| rating | `r18` |
| pov mode | `1p_user`, `3p_user` |
| `Item.visibility` | `private`, `public`, `hidden` |
| `Goal.status` | `active`, `completed`, `abandoned` |
| `Secret.status` | `hidden`, `hinted`, `revealed` |
| `StaticEvent.status` | `pending`, `foreshadowed`, `triggered`, `done` |
| `Event.memory_type` | `episodic`, `emotional`, `relational` |
| `Schedule.recurrence` | `weekly`, `once` |
| `day_of_weeks` | int 0=월 ~ 6=일 |
| scene types | **세계/시나리오별 `SCENE_TYPES` 키** (고정 아님 — 월드 설정에서 읽어올 것) |

`affinity`/`trust`/`importance`/`sensitivity`/`subtlety`/`progress`/`emotional_weight`/`prompt_priority`는
전부 정수(대개 0~10 또는 0~100). 정확한 상한은 코드에 강제 없음 — 툴이 권장 범위만 표시.

---

## 10. 상호참조 그래프 (편집기의 "관계형 뷰"용)

문자열 id로 연결되는 참조들. 툴은 이걸 드롭다운/링크로 만들어 오타·끊긴 참조를 없애야 한다.

```
Character.id  ←─ owner_id (Item, Goal, Secret, Schedule, Rule)
              ←─ char_id / audience_id (SpeechProfile)
              ←─ from/to (condition JSON)
              ←─ RELATIONSHIP 양끝 / EVENT_INVOLVES / INVOLVED_IN
Location.id   ←─ location_id (DynamicState, Item, Schedule, Rule, Event)
              ←─ PART_OF (Location 트리)
              ←─ default_location_id (Scenario)
Event.id      ←─ GOAL_RELATED_EVENT, ROOTED_IN(Secret), ANCHORS_MEMORY(Item), OF_EVENT(Memory)
SCENE_TYPES키 ←─ prompt/scenes/{key}.md, few_shot/{key}.md, SpeechProfile.scene_type, Rule.scene_type
```

전체 엣지 테이블(무속성, base.py): `HAS_PROFILE/HAS_INFO/HAS_PERSONALITY/HAS_STATE/HAS_INTIMATE/
HAS_WORKPLACE/HAS_DIALOGUE_EXAMPLES/LOCATED_AT/HAS_SPEECH_PROFILE/HAS_RELATIONSHIP_PROFILE/PROFILE_TARGET/
APPLIES_AT/RULE_FOR_CHARACTER/INVOLVED_IN/OCCURRED_AT/REMEMBERS/OF_EVENT/HAS_NEEDS/EVENT_INVOLVES/
PURSUES/GOAL_RELATED_EVENT/OWNS/GAVE/ANCHORS_MEMORY/HAS_SECRET/ROOTED_IN/TRIGGERED_BY/HAS_SCHEDULE/
SCHEDULED_AT/PART_OF/KNOWS_FACT/MEMBER_OF/ROOM_HAS_MESSAGE/SENT_KAKAO` + 속성 엣지 `RELATIONSHIP`.

---

## 11. 프롬프트 파일 계약 (markdown 측 편집)

`World.get_prompt_config(perspective)`가 파일들을 모아 다음 중첩 dict로 반환(편집기가 트리로 보여줄 대상):
```
pov        : {mode}                         # "1p_user" | "3p_user"
sections   : {world, prose, opening_scene, scenario}   # world.md / prose.md / scenarios/{sid}/*.md
characters : {focus, blacklist}             # prompt/characters/{id}.md (+ .cot_append.md)
scenes     : {prompt, blacklist}            # prompt/scenes/{scene}.md ∪ scenarios/{sid}/scenes/{scene}.md
blacklist  : {world, unified}               # cot_append.md
few_shot   : {scene: {good, bad}}           # few_shot/{scene}.md (parse_few_shot 형식)
```
- 키 매칭은 **`SCENE_TYPES` 키 = 파일명**이 규약. 씬 타입을 바꾸면 대응 md 파일도 같이 관리해야 한다.
- `opening_scene.md`는 `{char}`(NPC 한글명), `{user}`(PC 이름) 치환.
- 시나리오 전용 파일이 월드 공통보다 우선/머지된다.

---

## 12. 편집기 read/write 전략 (꼭 결정해야 하는 부분)

데이터가 §0-(1)처럼 명령형 Python에 박혀 있어, "실시간 편집"을 어떻게 영속화할지 길이 3개다:

| 전략 | 방식 | 장점 | 단점 |
|---|---|---|---|
| **A. 데이터화 마이그레이션** (권장) | 캐릭터/위치/관계를 `*.json`(또는 yaml)로 분리하고, 범용 `build_schema`가 그 데이터를 읽도록 리팩토링. 편집기는 JSON만 읽고 쓴다. | 편집기·검증·diff가 단순. key 강제 가능. 사람/AI 둘 다 안전 | 기존 세계 1회 마이그레이션 필요. JSON blob 노드 통합 작업 |
| **B. AST 추출/재생성** | 기존 `.py`의 `insert_*`/`conn.execute`/4-튜플 인자를 파싱→편집→재출력 | 코드 구조 유지 | 파서가 깨지기 쉬움. 자유형식 코드엔 취약 |
| **C. 그래프 라운드트립 MVP** | `schema_builder`로 Kuzu 빌드 → 그래프를 편집기에서 직접 CRUD | 가장 빠른 프로토타입, 실값 즉시 확인 | 원본 `.py`가 안 바뀜(소스 진실 불일치). thread DB와 분리 주의 |

→ 실시간 편집 + 지속이 목표면 **A**가 정석. 빠른 검증·시연이면 **C**로 시작해 A로 수렴 추천.
어느 쪽이든 본 문서의 §3~§10이 그대로 데이터 모델 정의가 된다.

---

## 13. 편집기가 지켜야 할 불변식 (검증 규칙)

1. `Character.id`는 세계 내 유일. 서브노드 id는 `{char_id}_{suffix}` 관례 유지.
2. 모든 `*_id` / 관계 target / condition `from`·`to`는 **존재하는 노드를 가리켜야** 한다(끊긴 참조 경고).
3. `RELATIONSHIP`은 방향성 — 양방향을 따로 관리하되 비대칭 허용. 한쪽만 있으면 경고(의도일 수 있음).
4. `prompt/scenes/{key}.md`의 key는 해당 시나리오 `SCENE_TYPES`에 존재해야 함.
5. JSON blob 노드(§3-B)는 자유 key 허용하되, **같은 의미의 key 표기 흔들림**(예 `biological_sex` vs
   `biological_sex_current`)을 감지·통일 제안.
6. 조건/`material`/`flags`는 유효한 JSON 문자열이어야 함.
7. `Scenario.default_location_id` / `default_time`은 해당 시나리오에 실제 생성되는 Location/시점과 일치.

---

### 부록: 새 세계 최소 골격 (참고)
```python
class MyWorld(World):
    WORLD_ID = "my_world"; DEFAULT_PERSPECTIVE = 3
    SCENE_TYPES = {"daily": "...", "intimate": "..."}
    def get_default_time(self): ...
    def get_default_location_id(self): ...
    def get_npc_name_map(self): ...          # 이름/별명 → char_id (NPC 감지)
    def build_schema(self, conn, scenario_id=None):
        self._build_tables(conn); self._build_locations(conn)
        self._build_characters(conn); self._build_relationships(conn)
        self.build_scenario_data(conn, scenario_id)

world_instance = MyWorld(narrator=Npc(), pc=Pc(), chars=[Pc(), Npc()])
SCENARIOS = [Scenario("default", "표시명", world=world_instance,
                      default_time=..., default_location_id=...)]
```
초기화: `python -m src.core.database.schema_builder`
