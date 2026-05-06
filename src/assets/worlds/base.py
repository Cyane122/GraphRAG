# ================================
# src/assets/worlds/base.py
#
# World 베이스 클래스. 모든 세계 구현체가 상속하는 인터페이스.
# world_section / specific_prose_rules / few_shot_examples /
# blacklist / npc_name_map / start_time 및 build_schema 정의.
#
# Classes
#   - World : 세계 구현체 베이스 클래스
#             SCENE_TYPES: dict[str, str] — 씬 타입 이름 → 영문 설명 (classifier 프롬프트에 주입)
#             get_scene_types() -> list[str]       : 타입 이름 목록 (내부 키 조회용)
#             get_scene_descriptions() -> dict[str, str] : 전체 dict (classifier 주입용)
# ================================

import json
from datetime import datetime

import kuzu

from src.core.embedding.encoder import EMBEDDING_DIM


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


class World:
    WORLD_ID = "default"

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

    def get_full_config(self, perspective: int = 3) -> dict:
        """프롬프트 조립에 필요한 전체 설정 딕셔너리를 반환합니다."""
        return {
            "world_section":        self.get_world_section(),
            "specific_prose_rules": self.get_specific_prose_rules(perspective),
            "prose_rules":          self.get_specific_prose_rules(perspective),
            "few_shot_examples":    self.get_few_shot_examples(perspective),
            "additional_blacklist": self.get_blacklist(),
            "start_time":           self.get_default_time(),
            "pc_id":                self.get_pc_id(),
            "npc_id":               self.get_npc_id(),
            "npc_name_kor":         self.npc_name_kor(),
            "default_location_id":  self.get_default_location_id(),
        }

    def get_default_location_id(self) -> str:
        """기본 위치 ID를 반환합니다."""
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        """NPC ID → 한국어 이름 매핑을 반환합니다."""
        return {"이름": "Name"}

    def get_pc_id(self) -> str:
        """PC 노드 ID를 반환합니다."""
        return "player"

    def get_npc_id(self) -> str:
        """NPC 노드 ID를 반환합니다."""
        return "npc"

    def npc_name_kor(self) -> str:
        """NPC 한국어 이름을 반환합니다."""
        return "엔피씨"

    def build_schema(self, conn: kuzu.Connection) -> None:
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
                condition STRING, energy DOUBLE, stress DOUBLE, current_task STRING,
                current_location STRING,
                PRIMARY KEY(id)
            )""",

            "CREATE NODE TABLE IF NOT EXISTS Location(id STRING, name STRING, description STRING, atmosphere STRING, current_chars STRING[], PRIMARY KEY(id))",

            "CREATE NODE TABLE IF NOT EXISTS GlobalState(id STRING, currentLocationId STRING, currentTime STRING, weather STRING, schedule_slot STRING, clients_done INT64, clients_total INT64, flags STRING, PRIMARY KEY(id))",

            f"""CREATE NODE TABLE IF NOT EXISTS Event(
                id STRING, summary STRING, timestamp STRING,
                location_id STRING, impact STRING,
                need_name STRING,
                importance INT64, decay_rate DOUBLE, summary_level INT64,
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
                importance INT64,
                distortion_level DOUBLE,
                summary_level INT64,
                created_at STRING,
                last_decayed_at STRING,
                PRIMARY KEY(id)
            )""",

            # 정적 프로파일 노드: 세계별로 속성 구조가 달라 JSON blob으로 저장
            "CREATE NODE TABLE IF NOT EXISTS StaticProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS Personality(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS IntimateProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS WorkplaceProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS DialogueExamples(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS Item(id STRING, name STRING, description STRING, owner_id STRING, PRIMARY KEY(id))",

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
        ]
        for ddl in node_tables:
            conn.execute(ddl)

        # ── 관계 테이블 ────────────────────────────────────────
        rel_tables = [
            "CREATE REL TABLE IF NOT EXISTS HAS_PROFILE(FROM Character TO StaticProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_PERSONALITY(FROM Character TO Personality)",
            "CREATE REL TABLE IF NOT EXISTS HAS_STATE(FROM Character TO DynamicState)",
            "CREATE REL TABLE IF NOT EXISTS HAS_DYNAMIC_STATE(FROM Character TO DynamicState)",
            "CREATE REL TABLE IF NOT EXISTS HAS_INTIMATE(FROM Character TO IntimateProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_WORKPLACE(FROM Character TO WorkplaceProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_DIALOGUE_EXAMPLES(FROM Character TO DialogueExamples)",
            "CREATE REL TABLE IF NOT EXISTS LOCATED_AT(FROM Character TO Location)",
            """CREATE REL TABLE IF NOT EXISTS RELATIONSHIP(
                FROM Character TO Character,
                type STRING, affinity INT64, trust INT64,
                duration STRING, origin STRING, current_status STRING,
                eun_seo_desire STRING, shared_events STRING[], last_interaction STRING
            )""",
            "CREATE REL TABLE IF NOT EXISTS INVOLVED_IN(FROM Character TO Event)",
            "CREATE REL TABLE IF NOT EXISTS OCCURRED_AT(FROM Event TO Location)",
            "CREATE REL TABLE IF NOT EXISTS REMEMBERS(FROM Character TO Memory)",
            "CREATE REL TABLE IF NOT EXISTS OF_EVENT(FROM Memory TO Event)",
            "CREATE REL TABLE IF NOT EXISTS HAS_NEEDS(FROM Character TO NeedsState)",
            "CREATE REL TABLE IF NOT EXISTS EVENT_INVOLVES(FROM StaticEvent TO Character)",
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
