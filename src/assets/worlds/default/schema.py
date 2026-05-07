# ================================
# src/assets/worlds/default/schema.py
#
# Default 세계 구현체. 새 세계관을 만들 때 복사·수정하는 템플릿.
# 최소한의 노드(Location, PC, NPC, DynamicState, Relationship)만 포함.
#
# Classes
#   - DefaultWorld : 기본 세계 구현체
# ================================

from datetime import datetime
from pathlib import Path

import kuzu

from src.assets.worlds.base import World, insert_static_inline
from src.assets.worlds.utils import read_md, parse_few_shot

_PROMPT_DIR = Path(__file__).parent / "prompt"


class DefaultWorld(World):
    WORLD_ID = "default"

    def get_default_time(self) -> datetime:
        """기본 시작 시각을 반환합니다."""
        return datetime(2025, 1, 1, 12, 0)

    def get_default_location_id(self) -> str:
        return "home"

    def get_world_section(self) -> str:
        return read_md(_PROMPT_DIR, "world.md")

    def get_blacklist(self) -> str:
        return read_md(_PROMPT_DIR, "blacklist.md")

    def get_specific_prose_rules(self, perspective: int = 3) -> str:
        return read_md(_PROMPT_DIR, "prose_1p.md" if perspective == 1 else "prose_3p.md")

    def get_few_shot_examples(self, perspective: int = 3) -> dict:
        """씬 타입별 퓨샷 예시를 반환합니다."""
        suffix = "_1p" if perspective == 1 else "_3p"
        result = {}
        for scene in ["daily", "emotional", "physical", "intimate"]:
            path  = _PROMPT_DIR / "few_shot" / f"{scene}{suffix}.md"
            entry = parse_few_shot(path)
            if entry["good"] or entry["bad"]:
                result[scene] = entry
        return result

    def get_full_config(self, perspective: int = 3) -> dict:
        res = super().get_full_config(perspective)
        res["additional_blacklist"] = self.get_blacklist()
        res["start_time"]           = self.get_default_time()
        res["prose_rules"]          = self.get_specific_prose_rules(perspective)
        res["few_shot_examples"]    = self.get_few_shot_examples(perspective)
        res["rating"]               = "r18"
        return res

    def get_npc_name_map(self) -> dict[str, str]:
        return {}

    def build_schema(self, conn: kuzu.Connection) -> None:
        """Default 세계 스키마 및 초기 데이터를 Kuzu에 삽입합니다."""
        super().build_schema(conn)

        # ── Location ──────────────────────────────────────────
        conn.execute("""
            CREATE (:Location {
                id:            "home",
                name:          "집",
                description:   "두 사람의 공간.",
                atmosphere:    "comfortable+private",
                current_chars: ["char", "player"]
            })
        """)

        # ── Character ─────────────────────────────────────────
        conn.execute("CREATE (:Character {id: 'char',   name: '캐릭터', aliases: [], type: 'npc'})")
        conn.execute("CREATE (:Character {id: 'player', name: '플레이어', aliases: [], type: 'pc'})")

        # ── NPC 프로파일 ──────────────────────────────────────
        insert_static_inline(conn, "char", "HAS_PROFILE", "StaticProfile", "char_static",
            age=20, gender="female",
            appearance="보통 체형, 단발머리",
            personality="밝고 활발함",
        )

        insert_static_inline(conn, "char", "HAS_PERSONALITY", "Personality", "char_personality",
            core_traits="bright+friendly+honest",
            speech_style="반말, 자연스러운 구어체",
        )

        conn.execute("""
            CREATE (:DynamicState {
                id:               "char_state",
                physical_condition: "healthy",
                mental_condition:   "stable",
                stress_level:       2,
                mood:               "calm",
                location_id:        "home"
            })
        """)
        conn.execute(
            "MATCH (c:Character {id: 'char'}), (d:DynamicState {id: 'char_state'}) CREATE (c)-[:HAS_STATE]->(d)"
        )

        # ── PC DynamicState ───────────────────────────────────
        conn.execute("""
            CREATE (:DynamicState {
                id:               "player_state",
                physical_condition: "healthy",
                mental_condition:   "stable",
                stress_level:       1,
                mood:               "calm",
                location_id:        "home"
            })
        """)
        conn.execute(
            "MATCH (c:Character {id: 'player'}), (d:DynamicState {id: 'player_state'}) CREATE (c)-[:HAS_STATE]->(d)"
        )

        # ── LOCATED_AT ────────────────────────────────────────
        for char_id in ["char", "player"]:
            conn.execute(
                "MATCH (c:Character {id: $c}), (l:Location {id: 'home'}) CREATE (c)-[:LOCATED_AT]->(l)",
                {"c": char_id},
            )

        # ── RELATIONSHIP ──────────────────────────────────────
        conn.execute("""
            MATCH (a:Character {id: "char"}), (b:Character {id: "player"})
            CREATE (a)-[:RELATIONSHIP {
                type:           "friend",
                affinity:       70,
                trust:          70,
                current_status: "comfortable"
            }]->(b)
        """)
        conn.execute("""
            MATCH (a:Character {id: "player"}), (b:Character {id: "char"})
            CREATE (a)-[:RELATIONSHIP {
                type:     "friend",
                affinity: 70,
                trust:    70
            }]->(b)
        """)

        print("✅ Default 스키마 초기화 완료")


from src.assets.worlds.default.characters import Char, Player  # noqa: E402
world_instance = DefaultWorld(
    narrator = Char(),
    pc       = Player(),
    chars    = [Char(), Player()],
)
