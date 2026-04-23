from datetime import datetime
from typing import Optional

from neo4j import GraphDatabase


class World:
    WORLD_ID = "default"

    def get_default_time(self) -> datetime:
        return datetime.now() # default = 현재 시간.

    def get_world_section(self) -> str:
        """기본 세계관 설명."""
        return """<world>
        # TITLE
        
        ## SUBTITLE
        </world>
        """

    def get_specific_prose_rules(self) -> str:
        """세계관/캐릭터별 특수 작법"""
        return """<character_specific_prose>
        # PROSE ARCHITECTURE
        
        ## Scene Structure
        </character_specific_prose>"""

    def get_few_shot_examples(self) -> dict:
        """대화 예시 퓨샷. Good/Bad로 나뉨."""
        return {
            "daily": {"good": [], "bad": []},
            # ... 다른 씬 타입들
        }

    def get_blacklist(self) -> str:
        return ""

    def get_full_config(self) -> dict:
        """모든 설정을 하나의 Dictionary로 묶어서 반환"""
        return {
            "world_section": self.get_world_section(),
            "specific_prose_rules": self.get_specific_prose_rules(),
            "few_shot_examples": self.get_few_shot_examples(),
            "additional_blacklist": self.get_blacklist(),
            "start_time": self.get_default_time(),
            "pc_id": self.get_pc_id(),
            "npc_id": self.get_npc_id(),
            "npc_name_kor": self.npc_name_kor(),
            "default_location_id": self.get_default_location_id()
        }

    def get_default_location_id(self) -> str:
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        return {
            "이름": "Name"
        }

    def get_pc_id(self) -> str:
        return "player"

    def get_npc_id(self) -> str:
        return "npc"

    def npc_name_kor(self) -> str:
        return "엔피씨"

    def build_schema(self, driver: GraphDatabase.driver):
        """
        모든 세계관의 뼈대가 되는 공통 DB 작업.
        1. 기존 DB 초기화 (경고: 모든 데이터 삭제!)
        2. 기본 노드 라벨에 대한 ID 유니크 제약조건 생성
        3. 단 하나뿐인 전역 상태(GlobalState) 노드 생성/확보
        """
        with driver.session() as session:
            # ── 초기화 ────────────────────────────────────────
            session.run("MATCH (n) DETACH DELETE n")

            # ── 제약조건 ──────────────────────────────────────
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
            ]
            for c in constraints:
                session.run(c)

            print(f"[{self.WORLD_ID}] 공통 노드 제약조건을 생성했습니다.")

            session.run("""
                MERGE (gs:GlobalState {id: 'singleton'})
                ON CREATE SET
                    gs.currentTime = $start_time,
                    gs.currentLocationId = null,
                    gs.weather = 'clear'
            """, start_time=datetime.now().isoformat())
            print(f"[{self.WORLD_ID}] 전역 상태 노드를 생성했습니다.")
