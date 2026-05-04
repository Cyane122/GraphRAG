# src/graph/world/default.py
"""
World 베이스 클래스.
world_section / specific_prose_rules / few_shot_examples /
blacklist / npc_name_map / start_time 등 인터페이스 정의.
모든 세계 모듈은 이 클래스를 상속해 오버라이드.
perspective 파라미터 추가 — 1인칭/3인칭 분기를 하위 클래스로 위임.
"""

from datetime import datetime

from neo4j import GraphDatabase

from src.utils.embedder import EMBEDDING_DIM


class World:
    WORLD_ID = "default"

    def get_default_time(self) -> datetime:
        return datetime.now()

    def get_world_section(self) -> str:
        return """<world>
# TITLE

## SUBTITLE
</world>"""

    def get_specific_prose_rules(self, perspective: int = 3) -> str:
        return """<character_specific_prose>
# PROSE ARCHITECTURE

## Scene Structure
</character_specific_prose>"""

    def get_few_shot_examples(self, perspective: int = 3) -> dict:
        return {
            "daily": {"good": [], "bad": []},
        }

    def get_blacklist(self) -> str:
        return ""

    def get_full_config(self, perspective: int = 3) -> dict:
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
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        return {"이름": "Name"}

    def get_pc_id(self) -> str:
        return "player"

    def get_npc_id(self) -> str:
        return "npc"

    def npc_name_kor(self) -> str:
        return "엔피씨"

    def build_schema(self, driver: GraphDatabase.driver):
        """
        공통 DB 작업:
        1. 기존 DB 초기화
        2. 노드 레이블 유니크 제약조건 생성
        3. Event / Memory Vector Index 생성
        4. GlobalState 노드 생성
        """
        with driver.session() as session:
            # ── 초기화 ────────────────────────────────────────
            session.run("MATCH (n) DETACH DELETE n")

            # ── 유니크 제약조건 ───────────────────────────────
            constraints = [
                "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Character)         REQUIRE c.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)              REQUIRE e.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (l:Location)           REQUIRE l.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Item)               REQUIRE i.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (s:StaticProfile)      REQUIRE s.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Personality)        REQUIRE p.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (d:DynamicState)       REQUIRE d.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:IntimateProfile)    REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (w:WorkplaceProfile)   REQUIRE w.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (x:DialogueExamples)   REQUIRE x.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (gs:GlobalState)       REQUIRE gs.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (m:Memory)             REQUIRE m.id IS UNIQUE",
            ]
            for c in constraints:
                session.run(c)
            print(f"[{self.WORLD_ID}] 노드 제약조건 생성 완료.")

            # ── Event Vector Index ────────────────────────────
            session.run(f"""
                CREATE VECTOR INDEX event_embeddings IF NOT EXISTS
                FOR (e:Event) ON (e.embedding)
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {EMBEDDING_DIM},
                        `vector.similarity_function`: 'cosine'
                    }}
                }}
            """)

            # ── Memory Vector Index ───────────────────────────
            session.run(f"""
                CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
                FOR (m:Memory) ON (m.embedding)
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {EMBEDDING_DIM},
                        `vector.similarity_function`: 'cosine'
                    }}
                }}
            """)

            print(f"[{self.WORLD_ID}] Vector Index 생성 완료 (dim={EMBEDDING_DIM}).")

            # ── GlobalState ───────────────────────────────────
            session.run(f"""
                MERGE (gs:GlobalState {{id: 'singleton'}})
                SET gs.currentLocationId = '{self.get_default_location_id()}',
                    gs.currentTime       = '{self.get_default_time().isoformat()}',
                    gs.weather           = 'Clear'
            """)
            print(f"[{self.WORLD_ID}] GlobalState 생성 완료.")

            session.run("CALL db.awaitIndexes()")