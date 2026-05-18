# ================================
# src/assets/worlds/base.py
#
# World 베이스 클래스. 모든 세계 구현체가 상속하는 인터페이스.
# world_section / specific_prose_rules / few_shot_examples /
# blacklist / npc_name_map / start_time 및 _build_tables / build_schema 정의.
#
# Classes
#   - Scenario : 한 세계 내의 시작 설정 (시간, 장소, 오프닝 씬 경로).
#   - World : 세계 구현체 베이스 클래스
#             SCENARIOS: dict[str, Scenario] — 시나리오 ID → Scenario. 비어있으면 단일 세계.
#             SCENE_TYPES: dict[str, str] — 씬 타입 이름 → 영문 설명 (classifier 프롬프트에 주입)
#             get_scene_types() -> list[str]           : 타입 이름 목록 (내부 키 조회용)
#             get_scene_descriptions() -> dict[str, str] : 전체 dict (classifier 주입용)
#             _build_tables(conn) -> None              : DDL 전용 (노드·관계 테이블, 벡터 인덱스, GlobalState)
#             build_schema(conn, scenario_id) -> None  : 기본 구현은 _build_tables + build_scenario_data 호출
#             build_scenario_data(conn, scenario_id) -> None : 시나리오별 초기 데이터 훅 (no-op)
# ================================

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import kuzu

from src.core.embedding.encoder import EMBEDDING_DIM

if TYPE_CHECKING:
    from src.assets.worlds.base_character import Character


# ── 시나리오 ───────────────────────────────────────────────────────

@dataclass
class Scenario:
    """한 세계 내의 시작 설정. SCENARIOS dict에 등록하면 ChatProfile 드롭다운에 노출됩니다."""
    scenario_id: str
    display_name: str
    default_time: datetime
    default_location_id: str
    opening_scene_path: str = "opening_scene.md"


# ── 정적 노드 헬퍼 ─────────────────────────────────────────────────
# StaticProfile / Personality / IntimateProfile / WorkplaceProfile /
# DialogueExamples 는 세계별로 속성 구조가 달라 JSON blob으로 저장합니다.
# 에이전트는 node.props 를 JSON 파싱해 사용합니다.

def insert_static(conn: kuzu.Connection, label: str, node_id: str, **props) -> None:
    """정적 데이터 노드를 JSON blob으로 삽입합니다."""
    conn.execute(
        f"CREATE (:{label} {{id: $id, props: $props}})",
        {"id": node_id, "props": json.dumps(props, ensure_ascii=False)},
    )

def insert_static_inline(conn: kuzu.Connection, char_id: str, rel: str, label: str, node_id: str, **props) -> None:
    """Character → 정적 노드 관계를 한 번에 생성합니다."""
    conn.execute(
        f"""
        MATCH (c:Character {{id: $char_id}})
        CREATE (c)-[:{rel}]->(n:{label} {{id: $node_id, props: $props}})
        """,
        {
            "char_id": char_id,
            "node_id": node_id,
            "props": json.dumps(props, ensure_ascii=False),
        },
    )


def insert_rule(conn: kuzu.Connection, rule_id: str, **props) -> None:
    """Create a generic Rule node used by prompt and time-rule systems."""
    values = {
        "id": rule_id,
        "name": "",
        "summary": "",
        "prompt_hint": "",
        "prompt_priority": 0,
        "tags": [],
        "location_id": "",
        "owner_id": "",
        "scene_type": "",
        "status": "active",
    }
    values.update(props)
    conn.execute(
        """
        CREATE (:Rule {
            id: $id,
            name: $name,
            summary: $summary,
            prompt_hint: $prompt_hint,
            prompt_priority: $prompt_priority,
            tags: $tags,
            location_id: $location_id,
            owner_id: $owner_id,
            scene_type: $scene_type,
            status: $status
        })
        """,
        values,
    )


def insert_schedule(conn: kuzu.Connection, owner_id: str, schedule_id: str, **props) -> None:
    """Create a Schedule node and attach it to the owning Character."""
    values = {
        "id": schedule_id,
        "owner_id": owner_id,
        "name": "",
        "activity": "",
        "summary": "",
        "prompt_hint": "",
        "prompt_priority": 0,
        "material": "",
        "recurrence": "weekly",
        "day_of_week": -1,
        "day_of_weeks": [],
        "date": "",
        "start_time": "",
        "end_time": "",
        "start_minute": -1,
        "end_minute": -1,
        "location_id": "",
        "status": "active",
        "tags": [],
    }
    values.update(props)
    day_values = _normalize_weekdays(values.get("day_of_weeks") or values.get("day_of_week"))
    values["day_of_weeks"] = day_values
    values["day_of_week"] = day_values[0] if day_values else -1
    values["start_minute"] = _normalize_minute(values.get("start_minute"), values.get("start_time"))
    values["end_minute"] = _normalize_minute(values.get("end_minute"), values.get("end_time"))
    create_values = {
        key: values[key]
        for key in (
            "id",
            "owner_id",
            "name",
            "activity",
            "summary",
            "prompt_hint",
            "prompt_priority",
            "material",
            "recurrence",
            "day_of_week",
            "date",
            "start_time",
            "end_time",
            "start_minute",
            "end_minute",
            "location_id",
            "status",
        )
    }
    conn.execute(
        """
        MATCH (c:Character {id: $owner_id})
        CREATE (c)-[:HAS_SCHEDULE]->(:Schedule {
            id: $id,
            owner_id: $owner_id,
            name: $name,
            activity: $activity,
            summary: $summary,
            prompt_hint: $prompt_hint,
            prompt_priority: $prompt_priority,
            material: $material,
            recurrence: $recurrence,
            day_of_week: $day_of_week,
            day_of_weeks: $day_of_weeks,
            date: $date,
            start_time: $start_time,
            end_time: $end_time,
            start_minute: $start_minute,
            end_minute: $end_minute,
            location_id: $location_id,
            status: $status
        })
        """,
        create_values,
    )
    conn.execute(
        """
        MATCH (s:Schedule {id: $id})
        SET s.day_of_weeks = $day_of_weeks,
            s.tags = $tags
        """,
        {
            "id": values["id"],
            "day_of_weeks": values["day_of_weeks"],
            "tags": values["tags"],
        },
    )
    if values["location_id"]:
        conn.execute(
            """
            MATCH (s:Schedule {id: $id}), (l:Location {id: $location_id})
            CREATE (s)-[:SCHEDULED_AT]->(l)
            """,
            {
                "id": values["id"],
                "location_id": values["location_id"],
            },
        )


def _normalize_weekdays(raw: object) -> list[int]:
    """Normalize one weekday or many weekdays into sorted Python weekday numbers."""
    if raw in (None, "", -1):
        return []
    if isinstance(raw, int):
        return [raw] if 0 <= raw <= 6 else []
    if isinstance(raw, (set, tuple, list)):
        days: set[int] = set()
        for value in raw:
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6:
                days.add(day)
        return sorted(days)
    try:
        day = int(raw)
    except (TypeError, ValueError):
        return []
    return [day] if 0 <= day <= 6 else []


def _normalize_minute(raw_minute: object, raw_time: object) -> int:
    """Use an explicit minute value or derive it from an HH:MM string."""
    try:
        minute = int(raw_minute)
    except (TypeError, ValueError):
        minute = -1
    if minute >= 0:
        return minute
    text = str(raw_time or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return -1
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return -1
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return -1
    return hour * 60 + minute


class World:
    WORLD_ID = "default"

    # 비어 있으면 이 세계는 단일 ChatProfile로 노출됩니다.
    # 항목이 있으면 각 Scenario가 별도 ChatProfile ('{world_id}/{scenario_id}')로 노출됩니다.
    SCENARIOS: dict[str, Scenario] = {}

    def __init__(
        self,
        narrator: Character | None = None,
        pc: Character | None = None,
        chars: list[Character] | None = None,
        perspective: int = 1,
    ) -> None:
        """세계 인스턴스를 초기화합니다.

        narrator: 서술자 캐릭터 (NPC POV). 미지정 시 get_npc_id 기본값 사용.
        pc: 플레이어 캐릭터. 미지정 시 get_pc_id 기본값 사용.
        chars: 이 세계에 존재하는 캐릭터 목록. build_schema 시 사용.
        """
        self.narrator = narrator
        self.pc = pc
        self.chars: list[Character] = chars or []
        self.perspective = perspective

    # classifier LLM에 주입할 씬 타입 정의. name → description(English).
    # 설명은 classifier 프롬프트에 직접 주입되므로 LLM이 타입을 정확히 구분할 수 있다.
    SCENE_TYPES: dict[str, str] = {
        "daily":       "Everyday life with no significant conflict (meals, casual movement, small talk)",
        "bonding":     "Emotional intimacy between romantic partners; warmth, vulnerability, closeness",
        "intimate":    "Romantic/sexual intercourse between lovers; emotionally connected",
        "formal":      "Meetings, negotiations, or situations requiring decorum and social performance",
        "tense":       "Pre-conflict chill; unspoken hostility or the moment before something breaks",
        "vulnerable":  "A character's weakness is exposed — emotional breakdown, exhaustion, confession",
        "aggressive":  "Verbal confrontation: arguments, threats, power struggles, psychological pressure",
        "physical":    "Combat, chase, training, or any scene driven by bodily exertion and kinetic energy",
        "atmospheric": "Environment or mood takes center stage; setting description, sensory immersion",
    }

    def get_scene_types(self) -> list[str]:
        """씬 타입 이름 목록만 반환합니다 (SCENE_REL_MAP 조회·퓨샷 키 매핑 등 내부 용도)."""
        return list(self.SCENE_TYPES.keys())

    def get_scene_descriptions(self) -> dict[str, str]:
        """씬 타입 이름 → 설명 매핑을 반환합니다 (classifier 프롬프트 주입용)."""
        return self.SCENE_TYPES

    def get_default_time(self) -> datetime:
        """기본 시작 시각을 반환합니다."""
        return datetime.now()

    def get_world_section(self) -> str:
        """세계관 설명 XML 섹션을 반환합니다."""
        return """<world>
# TITLE

## SUBTITLE
</world>"""

    def get_specific_prose_rules(self, perspective: int = 3) -> str:
        """시점별 작법 규칙 XML 섹션을 반환합니다."""
        return """<character_specific_prose>
# PROSE ARCHITECTURE

## Scene Structure
</character_specific_prose>"""

    def get_few_shot_examples(self, perspective: int = 3) -> dict:
        """씬 타입별 퓨샷 예시를 반환합니다."""
        return {
            "daily": {"good": [], "bad": []},
        }

    def get_blacklist(self) -> str:
        """블랙리스트 항목 문자열을 반환합니다."""
        return ""

    def get_full_config(self, perspective: int = 3, scenario_id: str | None = None) -> dict:
        """프롬프트 조립에 필요한 전체 설정 딕셔너리를 반환합니다."""
        scenario = self.SCENARIOS.get(scenario_id) if scenario_id and self.SCENARIOS else None
        start_time       = scenario.default_time        if scenario else self.get_default_time()
        default_location = scenario.default_location_id if scenario else self.get_default_location_id()
        return {
            "world_section":        self.get_world_section(),
            "specific_prose_rules": self.get_specific_prose_rules(perspective),
            "prose_rules":          self.get_specific_prose_rules(perspective),
            "few_shot_examples":    self.get_few_shot_examples(perspective),
            "additional_blacklist": self.get_blacklist(),
            "start_time":           start_time,
            "pc_id":                self.get_pc_id(),
            "npc_id":               self.get_npc_id(),
            "npc_name_kor":         self.npc_name_kor(),
            "default_location_id":  default_location,
            "scenario_id":          scenario_id,
        }

    def get_default_location_id(self) -> str:
        """기본 위치 ID를 반환합니다."""
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        """NPC ID → 한국어 이름 매핑을 반환합니다."""
        return {"이름": "Name"}

    def get_pc_id(self) -> str:
        """PC 노드 ID를 반환합니다. narrator/pc가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.pc.id if self.pc else "player"

    def get_npc_id(self) -> str:
        """NPC(서술자) 노드 ID를 반환합니다. narrator가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.narrator.id if self.narrator else "npc"

    def npc_name_kor(self) -> str:
        """NPC(서술자) 한국어 이름을 반환합니다. narrator가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.narrator.name if self.narrator else "엔피씨"

    def _build_world_events(self, conn: kuzu.Connection) -> None:
        """세계 레벨 StaticEvent를 생성합니다. 캐릭터 무관 이벤트(계절 전환, 대규모 사건 등).

        서브클래스에서 필요 시 오버라이드합니다. 기본 구현은 no-op.
        """

    def build_scenario_data(self, conn: kuzu.Connection, scenario_id: str | None) -> None:
        """시나리오별 초기 데이터를 삽입합니다. 기본 구현은 no-op.

        GlobalState 업데이트, 캐릭터 초기 위치 설정 등 시나리오 고유 초기화를 여기서 처리합니다.
        """

    def build_schema(self, conn: kuzu.Connection, scenario_id: str | None = None) -> None:
        """DDL + 시나리오 초기 데이터를 실행합니다."""
        self._build_tables(conn)
        self.build_scenario_data(conn, scenario_id)

    def _build_tables(self, conn: kuzu.Connection) -> None:
        """
        Kuzu 스키마를 초기화합니다.

        1. 노드 테이블 생성 (모든 세계 공통)
        2. 관계 테이블 생성
        3. 벡터 인덱스 생성
        4. GlobalState 노드 생성
        """
        dim = int(EMBEDDING_DIM or 1024)

        # ── 노드 테이블 ────────────────────────────────────────
        node_tables = [
            # 자주 쿼리되는 노드: 명시적 타입 컬럼
            "CREATE NODE TABLE IF NOT EXISTS Character(id STRING, name STRING, aliases STRING[], type STRING, PRIMARY KEY(id))",

            # DynamicState: helpers.py에서 직접 SET — 세계별 컬럼 차이를 모두 포괄
            """CREATE NODE TABLE IF NOT EXISTS DynamicState(
                id STRING,
                physical_condition STRING, mental_condition STRING,
                stress_level INT64, mood STRING, cycle_day INT64,
                location_id STRING, workplace_stress_level INT64,
                knee_condition STRING, injury_detail STRING,
                energy DOUBLE, stress DOUBLE, current_task STRING,
                outfit STRING, injury_marks STRING,
                has_menstrual_cycle BOOLEAN,
                pregnant BOOLEAN, pregnancy_day INT64, cum_shots_this_cycle INT64,
                body_perception STRING, behavioral_facade STRING,
                hygiene DOUBLE, appearance DOUBLE, physique STRING,
                age_presentation STRING, nervousness DOUBLE, attitude STRING,
                social_skill DOUBLE, consideration DOUBLE, stamina DOUBLE,
                odor STRING, emotional_state STRING, attachment_risk DOUBLE,
                expectation_gap DOUBLE, penis_size STRING,
                age INT64, circle_level INT64, robe_grade STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Location(
                id STRING,
                name STRING,
                description STRING,
                atmosphere STRING,
                district STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Rule(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                location_id STRING,
                owner_id STRING,
                scene_type STRING,
                status STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS SpeechProfile(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                char_id STRING,
                audience_id STRING,
                scene_type STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS RelationshipProfile(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                source_id STRING,
                target_id STRING,
                scene_type STRING,
                PRIMARY KEY(id)
            )""",

            "CREATE NODE TABLE IF NOT EXISTS GlobalState(id STRING, currentLocationId STRING, currentTime STRING, weather STRING, schedule_slot STRING, clients_done INT64, clients_total INT64, flags STRING, today_schedule STRING, schedule_date STRING, PRIMARY KEY(id))",

            f"""CREATE NODE TABLE IF NOT EXISTS Event(
                id STRING, summary STRING, timestamp STRING,
                location_id STRING, impact STRING,
                need_name STRING,
                importance INT64, decay_rate DOUBLE, summary_level INT64,
                memory_type STRING,
                narrative_summary STRING,
                state_summary STRING,
                safety_impact DOUBLE, safety_resolved BOOLEAN, safety_decay_rate DOUBLE,
                embedding FLOAT[{dim}],
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS NeedsState(
                id STRING,
                hunger DOUBLE, rest DOUBLE, social DOUBLE,
                fun DOUBLE, safety DOUBLE, libido DOUBLE,
                PRIMARY KEY(id)
            )""",

            f"""CREATE NODE TABLE IF NOT EXISTS Memory(
                id STRING,
                event_id STRING,
                char_id STRING,
                summary STRING,
                embedding FLOAT[{dim}],
                memory_type STRING,
                narrative_summary STRING,
                state_summary STRING,
                importance INT64,
                distortion_level DOUBLE,
                summary_level INT64,
                created_at STRING,
                last_decayed_at STRING,
                PRIMARY KEY(id)
            )""",

            # 정적 프로파일 노드: 세계별로 속성 구조가 달라 JSON blob으로 저장
            "CREATE NODE TABLE IF NOT EXISTS StaticProfile(id STRING, props STRING, age INT64, gender STRING, role STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS DynamicInformation(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS Personality(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS IntimateProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS WorkplaceProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS DialogueExamples(id STRING, props STRING, PRIMARY KEY(id))",
            """CREATE NODE TABLE IF NOT EXISTS Item(
                id STRING,
                name STRING,
                description STRING,
                owner_id STRING,
                location_id STRING,
                emotional_weight INT64,
                visibility STRING,
                last_seen_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Goal(
                id STRING,
                owner_id STRING,
                title STRING,
                description STRING,
                status STRING,
                progress INT64,
                subtlety INT64,
                next_hint STRING,
                trigger_conditions STRING,
                completion_conditions STRING,
                last_progressed_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Secret(
                id STRING,
                owner_id STRING,
                title STRING,
                private_summary STRING,
                public_hint STRING,
                status STRING,
                sensitivity INT64,
                reveal_conditions STRING,
                current_reveal_level INT64,
                last_hinted_at STRING,
                PRIMARY KEY(id)
            )""",

            # Schedule: recurring or one-off character routines rendered into dynamic context.
            """CREATE NODE TABLE IF NOT EXISTS Schedule(
                id STRING,
                owner_id STRING,
                name STRING,
                activity STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                material STRING,
                recurrence STRING,
                day_of_week INT64,
                day_of_weeks INT64[],
                date STRING,
                start_time STRING,
                end_time STRING,
                start_minute INT64,
                end_minute INT64,
                location_id STRING,
                status STRING,
                tags STRING[],
                PRIMARY KEY(id)
            )""",

            # StaticEvent: 조건 기반 이벤트. foreshadow → trigger 두 단계 조건으로 복선과 발화를 분리
            """CREATE NODE TABLE IF NOT EXISTS StaticEvent(
                id STRING,
                name STRING,
                foreshadow_conditions STRING,
                foreshadow_hint STRING,
                trigger_conditions STRING,
                status STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS PersonalFact(
                id STRING,
                subject_id STRING,
                audience_id STRING,
                category STRING,
                fact_text STRING,
                normalized_key STRING,
                status STRING,
                valid_from STRING,
                valid_until STRING,
                confidence DOUBLE,
                source STRING,
                created_at STRING,
                updated_at STRING,
                PRIMARY KEY(id)
            )""",
        ]
        for ddl in node_tables:
            conn.execute(ddl)

        # ── 관계 테이블 ────────────────────────────────────────
        rel_tables = [
            "CREATE REL TABLE IF NOT EXISTS HAS_PROFILE(FROM Character TO StaticProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_INFO(FROM Character TO DynamicInformation)",
            "CREATE REL TABLE IF NOT EXISTS HAS_PERSONALITY(FROM Character TO Personality)",
            "CREATE REL TABLE IF NOT EXISTS HAS_STATE(FROM Character TO DynamicState)",
            "CREATE REL TABLE IF NOT EXISTS HAS_INTIMATE(FROM Character TO IntimateProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_WORKPLACE(FROM Character TO WorkplaceProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_DIALOGUE_EXAMPLES(FROM Character TO DialogueExamples)",
            "CREATE REL TABLE IF NOT EXISTS LOCATED_AT(FROM Character TO Location)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SPEECH_PROFILE(FROM Character TO SpeechProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_RELATIONSHIP_PROFILE(FROM Character TO RelationshipProfile)",
            "CREATE REL TABLE IF NOT EXISTS PROFILE_TARGET(FROM RelationshipProfile TO Character)",
            "CREATE REL TABLE IF NOT EXISTS APPLIES_AT(FROM Rule TO Location)",
            "CREATE REL TABLE IF NOT EXISTS RULE_FOR_CHARACTER(FROM Rule TO Character)",
            """CREATE REL TABLE IF NOT EXISTS RELATIONSHIP(
                FROM Character TO Character,
                type STRING, affinity INT64, trust INT64,
                duration STRING, origin STRING, current_status STRING,
                summary STRING,
                eun_seo_desire STRING, shared_events STRING[], last_interaction STRING
            )""",
            "CREATE REL TABLE IF NOT EXISTS INVOLVED_IN(FROM Character TO Event)",
            "CREATE REL TABLE IF NOT EXISTS OCCURRED_AT(FROM Event TO Location)",
            "CREATE REL TABLE IF NOT EXISTS REMEMBERS(FROM Character TO Memory)",
            "CREATE REL TABLE IF NOT EXISTS OF_EVENT(FROM Memory TO Event)",
            "CREATE REL TABLE IF NOT EXISTS HAS_NEEDS(FROM Character TO NeedsState)",
            "CREATE REL TABLE IF NOT EXISTS EVENT_INVOLVES(FROM StaticEvent TO Character)",
            "CREATE REL TABLE IF NOT EXISTS PURSUES(FROM Character TO Goal)",
            "CREATE REL TABLE IF NOT EXISTS GOAL_RELATED_EVENT(FROM Goal TO Event)",
            "CREATE REL TABLE IF NOT EXISTS OWNS(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS GAVE(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS ANCHORS_MEMORY(FROM Item TO Memory)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SECRET(FROM Character TO Secret)",
            "CREATE REL TABLE IF NOT EXISTS ROOTED_IN(FROM Secret TO Event)",
            "CREATE REL TABLE IF NOT EXISTS TRIGGERED_BY(FROM Secret TO Item)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SCHEDULE(FROM Character TO Schedule)",
            "CREATE REL TABLE IF NOT EXISTS SCHEDULED_AT(FROM Schedule TO Location)",
            "CREATE REL TABLE IF NOT EXISTS PART_OF(FROM Location TO Location)",
            "CREATE REL TABLE IF NOT EXISTS KNOWS_FACT(FROM Character TO PersonalFact)",
        ]
        for ddl in rel_tables:
            conn.execute(ddl)

        print(f"[{self.WORLD_ID}] 노드/관계 테이블 생성 완료.")

        # ── 벡터 인덱스 (Kuzu v0.7+) ──────────────────────────
        try:
            conn.execute("CALL CREATE_VECTOR_INDEX('Event', 'event_embeddings', 'embedding')")
            conn.execute("CALL CREATE_VECTOR_INDEX('Memory', 'memory_embeddings', 'embedding')")
            print(f"[{self.WORLD_ID}] 벡터 인덱스 생성 완료 (dim={dim}).")
        except Exception as e:
            print(f"[{self.WORLD_ID}] 벡터 인덱스 생성 스킵: {e}")

        # ── GlobalState ────────────────────────────────────────
        conn.execute("""
            CREATE (:GlobalState {
                id: 'singleton',
                currentLocationId: $loc,
                currentTime: $time,
                weather: 'Clear',
                flags: '{}'
            })
        """, {"loc": self.get_default_location_id(), "time": self.get_default_time().isoformat()})
        print(f"[{self.WORLD_ID}] GlobalState 생성 완료.")
