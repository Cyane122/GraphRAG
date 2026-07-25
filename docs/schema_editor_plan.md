세계관 구성을 조금 더 원활하게 할 수 있도록 별도 프로그램을 만들고 싶어.

FastAPI + HTML + JS + CSS로 만들어줘. 우선 세계관 폴더는 다음과 같아.

PROJECT_ROOT / src / assets / worlds / {world_id}.

{world_id}/
├── __init__.py              # characters/schema를 재노출 (거의 비어 있음)
├── schema.py                # ★ World 서브클래스 + 모듈 끝의 SCENARIOS 리스트 + world_instance alias
├── characters/
│   ├── __init__.py          # 캐릭터 클래스 export
│   └── {char}.py            # 캐릭터 1명 = 1파일 (build_schema + build_relationship)
└── prompt/
    ├── world.md             # 세계 설정 (Fixed 프롬프트)
    ├── prose.md             # 작법 규칙
    ├── cot_append.md        # 월드 공통 CoT/blacklist
    ├── scenes/{scene}.md    # 씬 타입별 규칙 (+ {scene}.cot_append.md)
    ├── few_shot/{scene}.md  # 씬별 퓨샷 예시
    ├── characters/{id}.md   # 캐릭터 집중 프롬프트 (+ {id}.cot_append.md)
    └── scenarios/{sid}/
        ├── scenario.md          # 시나리오 스코프/톤/규칙
        ├── opening_scene.md     # 오프닝 내레이션 ({char}/{user} 치환)
        ├── cot_append.md        # (선택) 시나리오 CoT
        └── scenes/{scene}.md    # (선택) 시나리오 전용 씬 오버라이드

---

# 1. schema.py

schema.py의 기본 구성은 다음과 같다.

```python
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import kuzu
from src.assets.worlds.base import World, Scenario, insert_rule, insert_schedule
from src.assets.worlds.base_character import Character
from src.assets.worlds.{world_id}.characters import {CharA}, {CharB}
from src.assets.worlds.utils import read_md, read_md_map, read_optional_md, parse_few_shot

_PROMPT_DIR = Path(__file__).parent / "prompt"


class NamedWorld(World):
    WORLD_ID = "world_id"
    DEFAULT_PERSPECTIVE = (3, "char")
    # (1, "user"): 플레이어 캐릭터 1인칭. 사칭 가능.
    # (1, "char"): 특정 NPC 1인칭. 사칭 선택 가능.
    # (3, "user"): 플레이어 앵커 3인칭. 사칭 가능.
    # (3, "char"): NPC 앵커 전지적 3인칭. 사칭 선택 가능.

    SCENE_TYPES: dict[str, str] = {}

    def __init__(
        self,
        narrator: Character,
        pc: Character | None,
        chars: list[Character],
        perspective: tuple[int, str] | None = None,
        scenario_id: str | None = None,
    ) -> None:
        super().__init__(
            narrator=narrator, pc=pc, chars=chars,
            perspective=perspective or self.DEFAULT_PERSPECTIVE,
            scenario_id=scenario_id,
        )
        if scenario_id == "scenario_a":
            self.SCENE_TYPES = {
                "daily":   "Everyday life with no significant conflict.",
                "bonding": "Emotional intimacy between characters.",
            }
        elif scenario_id == "scenario_b":
            self.SCENE_TYPES = {
                "daily":    "...",
                "intimate": "...",
            }

    def get_default_time(self) -> datetime:
        if self.scenario_id == "scenario_a":
            return datetime(2024, 4, 9, 8, 17)
        return datetime(2024, 4, 9, 8, 17)

    def get_default_location_id(self) -> str:
        if self.scenario_id == "scenario_a":
            return "location_id_a"
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        # char.getAlias() → {이름/별명: char_id} dict 반환 (base_character.Character에 정의)
        npc_map: dict[str, str] = {}
        for char in self.chars:
            npc_map.update(char.getAlias())
        return npc_map

    def get_prompt_config(self) -> dict:
        """프롬프트 파일 전체를 하나의 중첩 딕셔너리로 반환합니다."""
        person, anchor = self.DEFAULT_PERSPECTIVE
        pov_mode = f"{person}p_{anchor}"          # → "1p_user", "3p_char" 등
        scene_keys = list(self.SCENE_TYPES)
        char_ids = list(getattr(self, "PROMPT_CHARACTER_IDS", ()))
        _scenario_dir = _PROMPT_DIR / "scenarios" / (self.scenario_id or "default")
        scenario_scene_dir = _scenario_dir / "scenes"
        return {
            "pov": {
                "mode": pov_mode,
            },
            "sections": {
                "world":         read_md(_PROMPT_DIR, "world.md"),
                "prose":         read_md(_PROMPT_DIR, "prose.md"),
                "opening_scene": read_optional_md(_scenario_dir / "opening_scene.md"),
                "scenario":      read_optional_md(_scenario_dir / "scenario.md"),
            },
            "characters": {
                "focus":     {cid: read_optional_md(_PROMPT_DIR / "characters" / f"{cid}.md") for cid in char_ids},
                "blacklist": {cid: read_optional_md(_PROMPT_DIR / "characters" / f"{cid}.cot_append.md") for cid in char_ids},
            },
            "scenes": {
                "prompt": {
                    **read_md_map(_PROMPT_DIR / "scenes", scene_keys),
                    **read_md_map(scenario_scene_dir, scene_keys),
                },
                "blacklist": {
                    **read_md_map(_PROMPT_DIR / "scenes", scene_keys, suffix=".cot_append.md"),
                    **read_md_map(scenario_scene_dir, scene_keys, suffix=".cot_append.md"),
                },
            },
            "blacklist": {
                "world":   read_optional_md(_PROMPT_DIR / "cot_append.md"),
                "unified": True,
            },
            "few_shot": {k: parse_few_shot(_PROMPT_DIR / "few_shot" / f"{k}.md") for k in scene_keys},
        }

    def get_full_config(self, perspective: tuple[int, str] | None = None, scenario_id: str | None = None) -> dict:
        res = super().get_full_config(perspective or self.DEFAULT_PERSPECTIVE, scenario_id)
        res.update({
            "rating":           "r18",
            "world_cot_append": read_optional_md(
                _PROMPT_DIR / "scenarios" / (self.scenario_id or "default") / "cot_append.md"
            ) or read_optional_md(_PROMPT_DIR / "cot_append.md"),
            "prompt": self.get_prompt_config(),
        })
        return res

    # ── 시드 데이터 빌드 ─────────────────────────────────────────

    def _build_seed_events(self, conn: kuzu.Connection) -> None:
        char_ids = {c.id for c in self.chars}   # ← set[str] 로 비교

        _EVENTS = [
            {
                "id":        "event_id",
                "summary":   "등장인물 A가 처음 이곳에 온 날.",
                "timestamp": "2024-04-09T08:30:00",
                # importance: 1~10
                "importance": 7,
                # impact: "character_introduction" | "bonding_moment" | "conflict" | "intimate"
                "impact":     "character_introduction",
                # memory_type: "episodic" | "relational" | "emotional"
                "memory_type": "relational",
                # decay_rate: 0.05 느림(중요) | 0.15 보통 | 0.3 빠름
                "decay_rate":  0.05,
                # summary_level: 생략 시 0 (manager가 자동 갱신)
                "summary_level": 0,
                "narrative_summary": "",
                "state_summary":     "",
                "_involved":    ["char_id_a", "char_id_b"],  # 모두 있어야 생성됨
                "_location_id": "location_id",
            },
        ]

        for ev in _EVENTS:
            involved    = ev.pop("_involved")
            location_id = ev.pop("_location_id")
            if not set(involved).issubset(char_ids):
                continue
            conn.execute(
                """CREATE (:Event {
                    id: $id, summary: $summary, timestamp: $timestamp,
                    importance: $importance, impact: $impact,
                    memory_type: $memory_type, decay_rate: $decay_rate,
                    narrative_summary: $narrative_summary,
                    state_summary: $state_summary,
                    summary_level: $summary_level, embedding: NULL
                })""",
                ev,
            )
            for char_id in involved:
                conn.execute(
                    "MATCH (c:Character {id: $cid}), (e:Event {id: $eid}) CREATE (c)-[:INVOLVED_IN]->(e)",
                    {"cid": char_id, "eid": ev["id"]},
                )
            conn.execute(
                "MATCH (e:Event {id: $eid}), (l:Location {id: $lid}) CREATE (e)-[:OCCURRED_AT]->(l)",
                {"eid": ev["id"], "lid": location_id},
            )

    def _build_locations(self, conn: kuzu.Connection) -> None:
        _LOCATIONS: list[tuple] = [
            (
                "location_id",     # id: snake_case 고유 식별자
                "한국어 이름",       # name
                "English description.",  # description
                "Prompt hint.",    # prompt_hint: 장소 분위기·특징 (프롬프트에 주입)
                15,                # prompt_priority: 높을수록 우선 렌더링
                ["tag1", "tag2"],  # tags: 쿼리 필터용 (예: ["school", "daily", "floor_1"])
                ["linked_id"],     # links: PART_OF로 연결할 상위/인접 장소 id 목록
                ["scenario_a"],    # scenarios: 생성할 시나리오 id 목록 (비어있으면 모든 시나리오)
            ),
        ]

        # 1. 노드 생성
        for loc_id, name, desc, hint, priority, tags, _, scenarios in _LOCATIONS:
            if scenarios and self.scenario_id not in scenarios:
                continue
            conn.execute(
                """CREATE (:Location {
                    id: $id, name: $name, description: $description,
                    prompt_hint: $prompt_hint, prompt_priority: $priority, tags: $tags
                })""",
                {"id": loc_id, "name": name, "description": desc,
                 "prompt_hint": hint, "priority": priority, "tags": tags},
            )

        # 2. PART_OF 연결
        for loc_id, _, _, _, _, _, links, scenarios in _LOCATIONS:
            if scenarios and self.scenario_id not in scenarios:
                continue
            for linked_id in links:
                conn.execute(
                    "MATCH (a:Location {id: $a}), (b:Location {id: $b}) CREATE (a)-[:PART_OF]->(b)",
                    {"a": loc_id, "b": linked_id},
                )

    def _build_rule(self, conn: kuzu.Connection) -> None:
        _RULES: list[tuple] = [
            (
                "rule_id",
                "규칙 이름",
                "summary: 규칙 한 줄 요약.",
                "prompt_hint: 프롬프트에 주입될 지시문.",
                20,                # prompt_priority
                ["time_rule"],     # tags
                "location_id",     # 해당 장소에서만 적용 (빈 문자열 = 전역)
                ["scenario_a"],    # 해당 시나리오에서만 생성 (비어있으면 모든 시나리오)
            ),
        ]
        for rule_id, name, summary, prompt_hint, priority, tags, location_id, scenarios in _RULES:
            if scenarios and self.scenario_id not in scenarios:
                continue
            insert_rule(
                conn,
                rule_id=rule_id, name=name, summary=summary,
                prompt_hint=prompt_hint, prompt_priority=priority,
                tags=tags, location_id=location_id,
            )

    def build_schema(self, conn: kuzu.Connection, scenario_id: str | None = None) -> None:
        self._build_tables(conn)          # ← DDL 먼저
        self._build_locations(conn)
        self._build_rule(conn)
        self._build_seed_events(conn)
        for char in self.chars:
            char.build_schema(conn)
        for char in self.chars:
            for other in self.chars:
                if char.id != other.id:
                    char.build_relationship(conn, other)
        self.build_scenario_data(conn, scenario_id)


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="scenario_a",
        display_name="표기될 한글 한 줄 설명",
        world=NamedWorld(
            narrator=CharA(),
            pc=CharA(),
            chars=[CharA(), CharB()],
            scenario_id="scenario_a",
        ),
    ),
]

world_instance = SCENARIOS[0].world  # schema_builder 하위호환용
```

---

# 2. characters/{char}.py

캐릭터 1명 = 1파일. `Character` 베이스를 상속해 `build_schema` / `build_relationship`을 구현한다.

**노드 구조 (4-tier):**
```
Character
├─ HAS_STATIC_INFO ──→ StaticInformation   [JSON blob] 불변 배경
├─ HAS_PERSONALITY ──→ Personality         [JSON blob] 성격 (drift 대상)
├─ HAS_INFO ─────────→ DynamicInformation  [JSON blob] 천천히 변함
└─ HAS_STATE ────────→ DynamicState        [고정 컬럼] 자주 변함
```
`SpeechProfile`, `RelationshipProfile`, `IntimateProfile`, `WorkplaceProfile`, `DialogueExamples`는 폐기.
`Personality`는 별도 노드로 유지 — personality drift 시스템이 이 노드를 직접 읽고 쓴다.

**JSON blob 노드 (StaticInformation · Personality · DynamicInformation)의 커스텀 키:**
- 표준 키(아래 템플릿)는 기본 제공 필드.
- 세계관·캐릭터에 특화된 키는 **자유롭게 추가/삭제** 가능.
- 편집기는 표준 키를 기본 노출하고, "+ 키 추가" / "키 삭제" 버튼을 제공한다.
- 삭제 시 해당 키가 다른 쿼리에서 참조되는지 경고.

```python
# ================================
# src/assets/worlds/{world_id}/characters/{char_id}.py
#
# Classes
#   - {CharName}: {설명 한 줄}
# ================================

from __future__ import annotations
import json
import kuzu
from src.assets.worlds.base import insert_static_inline, insert_schedule
from src.assets.worlds.base_character import Character, _insert_rel


class CharName(Character):
    id   = "char_id"
    name = "한글이름"
    aliases = ["별명1", "별명2"]
    char_type = "npc"   # "PC" | "npc"

    # 시나리오별 분기 값 (변하는 값만 delta로 적음)
    DEFAULT_CFG: dict = {}
    SCENARIO_OVERRIDES: dict[str, dict] = {
        # "scenario_id": {"key": "delta_value"},
    }

    # ── build_schema ─────────────────────────────────────────────

    def build_schema(self, conn: kuzu.Connection) -> None:
        conn.execute(
            "CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: $type})",
            {"id": self.id, "name": self.name, "aliases": self.aliases, "type": self.char_type},
        )

        # StaticInformation: 불변 배경
        # 출생·성별·출신·가족관계·성장 배경 등 절대 바뀌지 않는 속성
        insert_static_inline(
            conn, self.id, "HAS_STATIC_INFO", "StaticInformation", f"{self.id}_static",
            birth_year=2008,
            biological_sex="Female",
            # TS 세계관: biological_sex_original / biological_sex_current 로 분리
            nationality="Korean",
            origin_city="Seoul",
            family="...",
            formative_background="...",
            # 세계관·캐릭터 특화 키 자유 추가 가능
            # intimate_profile="...",   # 성적 취향 등 불변 속성 (필요 시)
        )

        # Personality: 성격 (drift 가능)
        # personality drift 시스템이 이 노드를 직접 갱신함
        # core_traits: "+" 구분자로 연결한 태그열 (예: "energetic+impulsive+caring")
        insert_static_inline(
            conn, self.id, "HAS_PERSONALITY", "Personality", f"{self.id}_personality",
            core_traits="trait_a+trait_b+trait_c",
            speech_style="...",
            # 세계관·캐릭터 특화 키 자유 추가 가능
        )

        # DynamicInformation: 천천히 변하는 특성
        # 외모·스킬·현재 평판·성경험 등 게임 진행에 따라 바뀔 수 있는 속성
        insert_static_inline(
            conn, self.id, "HAS_INFO", "DynamicInformation", f"{self.id}_info",
            age=17,
            height="162cm",
            weight="46kg",
            # measurements: 여성(biological_sex == "Female" 또는 biological_sex_current == "Female")일 때만 활성화
            measurements="...",
            appearance="...",
            body_type="...",
            skills="...",
            hobby="...",
            current_reputation="...",
            complex="...",
            ideal_type="...",
            sexual_information="...",  # 현재 성경험 수준 (바뀔 수 있음)
            # 세계관·캐릭터 특화 키 자유 추가 가능
        )

        # DynamicState: 자주 변하는 현재 상태
        # 시뮬레이션이 매 턴 업데이트하는 값들
        _state: dict = {
            "id":                   f"{self.id}_state",
            "mood":                 "...",
            "emotional_state":      "...",
            "stress_level":         2,           # int 0~10
            "location_id":          "location_id",
            "outfit":               "school uniform",
            "has_menstrual_cycle":  True,
            "cycle_day":            14,           # 생리 주기 day
            "pregnant":             False,
            "pregnancy_day":        0,
            "cum_shots_this_cycle": 0,
        }
        conn.execute(
            """CREATE (:DynamicState {
                id: $id, mood: $mood, emotional_state: $emotional_state,
                stress_level: $stress_level, location_id: $location_id, outfit: $outfit,
                has_menstrual_cycle: $has_menstrual_cycle, cycle_day: $cycle_day,
                pregnant: $pregnant, pregnancy_day: $pregnancy_day,
                cum_shots_this_cycle: $cum_shots_this_cycle
            })""",
            _state,
        )
        conn.execute(
            "MATCH (c:Character {id: $id}), (d:DynamicState {id: $did}) CREATE (c)-[:HAS_STATE]->(d)",
            {"id": self.id, "did": f"{self.id}_state"},
        )

        # ── Item (선택) ───────────────────────────────────────────
        # emotional_weight 0~10: 높을수록 감정적으로 중요한 물건
        # visibility: "private" | "public" | "hidden"
        conn.execute(
            """CREATE (:Item {
                id: $id, name: $name, description: $description,
                owner_id: $owner_id, location_id: $location_id,
                emotional_weight: $emotional_weight,
                visibility: $visibility, last_seen_at: ""
            })""",
            {
                "id": "item_id", "name": "물건 이름", "description": "...",
                "owner_id": self.id, "location_id": "location_id",
                "emotional_weight": 7, "visibility": "private",
            },
        )
        conn.execute(
            "MATCH (c:Character {id: $cid}), (i:Item {id: $iid}) CREATE (c)-[:OWNS]->(i)",
            {"cid": self.id, "iid": "item_id"},
        )

        # ── Goal (선택) ───────────────────────────────────────────
        # progress 0~100 / subtlety 0~10 (낮을수록 숨겨진 목표)
        # status: "active" | "completed" | "abandoned"
        # trigger_conditions / completion_conditions: JSON 조건 배열 또는 자연어 문자열
        conn.execute(
            """CREATE (:Goal {
                id: $id, owner_id: $owner_id,
                title: $title, description: $description,
                status: $status, progress: $progress, subtlety: $subtlety,
                next_hint: $next_hint,
                trigger_conditions: $trigger_conditions,
                completion_conditions: $completion_conditions,
                last_progressed_at: $last_progressed_at
            })""",
            {
                "id": "goal_id", "owner_id": self.id,
                "title": "목표 제목", "description": "...",
                "status": "active", "progress": 10, "subtlety": 5,
                "next_hint": "다음 단서",
                "trigger_conditions": "",
                "completion_conditions": "조건 달성 시 완료.",
                "last_progressed_at": "2024-04-09T08:30:00",
            },
        )
        conn.execute(
            "MATCH (c:Character {id: $cid}), (g:Goal {id: $gid}) CREATE (c)-[:PURSUES]->(g)",
            {"cid": self.id, "gid": "goal_id"},
        )

        # ── Secret (선택) ─────────────────────────────────────────
        # sensitivity 0~10 / status: "hidden" | "hinted" | "revealed"
        # reveal_conditions: JSON 조건 배열
        #   예: '[{"type":"stat","field":"trust","op":">=","value":85,"from":"a","to":"b"}]'
        #   빈 문자열 = 조건 없음
        conn.execute(
            """CREATE (:Secret {
                id: $id, owner_id: $owner_id,
                title: $title,
                private_summary: $private_summary,
                public_hint: $public_hint,
                status: $status, sensitivity: $sensitivity,
                reveal_conditions: $reveal_conditions,
                current_reveal_level: $current_reveal_level,
                last_hinted_at: $last_hinted_at
            })""",
            {
                "id": "secret_id", "owner_id": self.id,
                "title": "비밀 제목",
                "private_summary": "실제 비밀 내용.",
                "public_hint": "겉으로 새어나오는 단서.",
                "status": "hidden", "sensitivity": 8,
                "reveal_conditions": '[{"type":"stat","field":"trust","op":">=","value":85,"from":"char_id","to":"park_sian"}]',
                "current_reveal_level": 0,
                "last_hinted_at": "",
            },
        )
        conn.execute(
            "MATCH (c:Character {id: $cid}), (s:Secret {id: $sid}) CREATE (c)-[:HAS_SECRET]->(s)",
            {"cid": self.id, "sid": "secret_id"},
        )

        # ── Schedule (선택) ───────────────────────────────────────
        # recurrence: "weekly" | "once"
        # day_of_weeks: set[int] (0=월 ~ 6=일)
        # material: JSON 문자열 (자유 형식 추가 데이터)
        insert_schedule(
            conn,
            owner_id=self.id,
            schedule_id=f"{self.id}_schedule_name",
            name="스케줄 이름",
            activity="activity_key",
            recurrence="weekly",
            day_of_weeks={1, 4},       # 화·금
            start_time="18:00",
            end_time="21:00",
            location_id="location_id",
            prompt_hint="프롬프트 힌트",
            prompt_priority=10,
            tags=["tag"],
            material=json.dumps({"key": "value"}),
        )

    # ── build_relationship ────────────────────────────────────────

    def build_relationship(self, conn: kuzu.Connection, other: Character) -> None:
        _RELS: dict[str, tuple[str, int, int, str]] = {
            # "other_id": ("rel_type", affinity, trust, "관계 서술"),
            #
            # affinity (0~100): 호감도. trust 80 이상부터 오를 수 있다.
            #   대부분 0이 초기값. 이성 사이: 사랑 / 동성 사이: 찐친 느낌.
            #   오래된 관계·가족처럼 처음부터 값이 필요한 경우에만 실제값 입력.
            #
            # trust (0~100): 신뢰도. 실질 시드값.
            #   stranger ≈ 5 / 아는 사이 ≈ 20~40 / 친구 ≈ 60~75 / 절친 ≈ 80~88
            #
            # 4번째 문자열 → current_status 컬럼.
            #   변수명과 달리 긴 관계 서술문이다.
            #   A→B 와 B→A 는 별개 엣지 — 비대칭 허용.
            "other_char_id": ("best_friend", 0, 80, "관계 서술..."),
        }
        if other.id not in _RELS:
            return
        rel_type, affinity, trust, status = _RELS[other.id]
        _insert_rel(conn, self.id, other.id, rel_type, affinity, trust, status)
```

**base_character.py 추가 메서드 (`getAlias`):**
```python
def getAlias(self) -> dict[str, str]:
    """이름·별명 → char_id 매핑 반환. get_npc_name_map에서 사용."""
    return {alias: self.id for alias in [self.name] + list(self.aliases)}
```

---

# 3. prompt/ — .md 파일 편집

## 3-1. 파일 종류와 역할

| 경로 | 역할 | 프롬프트 내 위치 |
|---|---|---|
| `world.md` | 세계 설정 (배경·규칙·분위기) | Fixed — 턴 간 불변 (캐시 히트 대상) |
| `prose.md` | 작법 규칙 (문체·시점·묘사 지침) | Fixed |
| `cot_append.md` | 월드 공통 CoT·blacklist | Fixed |
| `scenes/{scene}.md` | 씬 타입별 행동 규칙 | Genre — 씬 타입마다 교체 |
| `scenes/{scene}.cot_append.md` | 씬별 blacklist | Genre |
| `few_shot/{scene}.md` | 씬별 퓨샷 예시 | Genre |
| `characters/{id}.md` | 특정 캐릭터 집중 프롬프트 | Fixed |
| `characters/{id}.cot_append.md` | 특정 캐릭터 blacklist | Fixed |
| `scenarios/{sid}/scenario.md` | 시나리오 스코프·톤·규칙 | Fixed |
| `scenarios/{sid}/opening_scene.md` | 세션 시작 내레이션 | Dynamic (첫 턴만) |
| `scenarios/{sid}/cot_append.md` | 시나리오 CoT | Fixed |
| `scenarios/{sid}/scenes/{scene}.md` | 시나리오 전용 씬 오버라이드 | Genre (월드 공통보다 우선) |

## 3-2. 씬 타입 키 매칭 규칙

`SCENE_TYPES`의 키 = `scenes/{key}.md` 파일명 = `few_shot/{key}.md` 파일명.
씬 타입을 추가하면 대응 .md 파일도 같이 생성해야 한다.

## 3-3. 변수 치환 (opening_scene.md 전용)

| 변수 | 치환 값 |
|---|---|
| `{char}` | `world.npc_name_kor()` — NPC 한글 이름 |
| `{user}` | `pc.name` — 플레이어 캐릭터 이름 |

## 3-4. few_shot 포맷

`few_shot/{scene}.md`는 `parse_few_shot(path)` 함수가 파싱한다.
`good`/`bad` 두 섹션으로 구성:

```markdown
## good

(좋은 예시 내용)

## bad

(나쁜 예시 내용)
```

반환값: `{"good": "...", "bad": "..."}` (섹션이 없으면 빈 문자열)

## 3-5. 편집기가 지원해야 할 기능

- **트리 뷰**: `prompt/` 디렉터리를 파일 트리로 표시
  - 씬 타입 키와 파일 존재 여부를 색상으로 구분 (키 있음 + 파일 없음 → 경고)
  - `scenarios/{sid}/scenes/`는 월드 공통 `scenes/`와 나란히 비교 가능하도록
- **Monaco 에디터**: 마크다운 문법 강조 + 미리보기 패널
- **변수 힌트**: `opening_scene.md` 편집 시 `{char}`, `{user}` 자동완성
- **few_shot 구조 검사**: `## good` / `## bad` 섹션 존재 여부 경고
- **씬 타입 동기화**: `SCENE_TYPES` 키 추가 시 대응 파일 생성 안내

## 3-6. 기타
- 폰트 사용: Pretendard.
- 전체 컨셉: PPT 슬라이드 느낌, 넓은 카드, 충분한 여백
- 레이아웃: 상단 바(세계관 선택/시나리오 탭/저장) + 사이드바 + 메인 편집 영역
- 캐릭터 카드: Static/Personality/Dynamic/State 탭 전환, 커스텀 키 영역 별도 구분
- 관계 그래프: Cytoscape.js, A→B / B→A 나란히 표시, 엣지 클릭 시 trust/affinity 편집
- 인터랙션: 명시적 저장 버튼, id 필드 드롭다운, 조건 JSON 빌더 토글