# ================================
# src/assets/worlds/default/schema.py
#
# Default 세계 구현체. 새 세계관을 만들 때 복사·수정하는 템플릿.
# 최소한의 노드(Location, PC, NPC, DynamicState, Relationship, generic prompt nodes)만 포함.
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


def _read_optional_md(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_md_map(directory: Path, keys: list[str], suffix: str = ".md") -> dict[str, str]:
    result: dict[str, str] = {}
    for key in keys:
        text = _read_optional_md(directory / f"{key}{suffix}")
        if text:
            result[key] = text
    return result


def _read_few_shot_map(directory: Path, keys: list[str], suffix: str = "") -> dict:
    result = {}
    for key in keys:
        entry = parse_few_shot(directory / f"{key}{suffix}.md")
        if entry["good"] or entry["bad"]:
            result[key] = entry
    return result


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

    def get_prompt_config(self, perspective: int = 3) -> dict:
        scene_keys = ["daily", "emotional", "physical", "intimate"]
        suffix = "_1p" if perspective == 1 else "_3p"
        return {
            "pov": {
                "mode": "1p_char" if perspective == 1 else "3p_char",
            },
            "sections": {
                "world": self.get_world_section(),
                "prose": self.get_specific_prose_rules(perspective),
            },
            "characters": {
                "focus": {},
                "blacklist": {},
            },
            "scenes": {
                "prompt": {},
                "blacklist": {},
            },
            "blacklist": {
                "world": self.get_blacklist(),
                "unified": True,
            },
            "few_shot": _read_few_shot_map(_PROMPT_DIR / "few_shot", scene_keys, suffix),
        }

    def get_full_config(self, perspective: int = 3, scenario_id: str | None = None) -> dict:
        res = super().get_full_config(perspective, scenario_id)
        res["start_time"] = self.get_default_time()
        res["rating"]     = "r18"
        res["prompt"]     = self.get_prompt_config(perspective)
        return res

    def get_npc_name_map(self) -> dict[str, str]:
        return {}

    def build_schema(self, conn: kuzu.Connection, scenario_id: str | None = None) -> None:
        """Default 세계 스키마 및 초기 데이터를 Kuzu에 삽입합니다."""
        super().build_schema(conn, scenario_id)

        # ── Location ──────────────────────────────────────────
        conn.execute("""
            CREATE (:Location {
                id:            "home",
                name:          "집",
                description:   "두 사람의 공간.",
                atmosphere:    "comfortable+private",
                summary:       "A quiet shared home for low-stakes daily scenes.",
                prompt_hint:   "집은 사적인 공간이다. 캐릭터는 긴장을 낮추고 자연스럽게 움직이며, 큰 사건보다 생활감 있는 물건과 작은 행동으로 반응한다.",
                prompt_priority: 10,
                tags:          ["home", "private", "daily"]
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

        # ── Generic prompt nodes ──────────────────────────────
        conn.execute("""
            CREATE (:Rule {
                id:              "default_home_privacy",
                name:            "집의 사적 분위기",
                summary:         "집에서는 과장된 사건보다 일상적 반응과 편안한 거리감이 우선이다.",
                prompt_hint:     "집 장면에서는 캐릭터가 갑자기 극적인 사건을 만들지 않는다. 작은 생활 행동, 시선, 말끝으로 관계 변화를 보여준다.",
                prompt_priority: 10,
                tags:            ["home", "daily"],
                location_id:     "home",
                owner_id:        "char",
                scene_type:      "daily",
                status:          "active"
            })
        """)
        conn.execute("""
            CREATE (:SpeechProfile {
                id:              "char_speech_player_daily",
                name:            "캐릭터 기본 말투",
                summary:         "플레이어에게 편하게 반말하는 자연스러운 구어체.",
                prompt_hint:     "캐릭터는 플레이어에게 반말을 쓴다. 설명조보다 짧고 자연스러운 구어체로 말하고, 감정은 말보다 행동에 먼저 실린다.",
                prompt_priority: 10,
                tags:            ["daily", "casual"],
                char_id:         "char",
                audience_id:     "player",
                scene_type:      ""
            })
        """)
        conn.execute("""
            CREATE (:RelationshipProfile {
                id:              "char_player_comfortable_friend",
                name:            "편한 친구 관계",
                summary:         "서로 편하지만 아직 큰 갈등이나 고백이 없는 안정적 관계.",
                prompt_hint:     "캐릭터는 플레이어에게 편하게 기대되, 플레이어의 행동을 대신 만들지 않는다. 친밀감은 농담, 작은 배려, 익숙한 거리감으로 드러낸다.",
                prompt_priority: 10,
                tags:            ["friend", "comfortable"],
                source_id:       "char",
                target_id:       "player",
                scene_type:      ""
            })
        """)
        conn.execute("""
            MATCH (c:Character {id: "char"}), (s:SpeechProfile {id: "char_speech_player_daily"})
            CREATE (c)-[:HAS_SPEECH_PROFILE]->(s)
        """)
        conn.execute("""
            MATCH (c:Character {id: "char"}), (rp:RelationshipProfile {id: "char_player_comfortable_friend"}), (p:Character {id: "player"})
            CREATE (c)-[:HAS_RELATIONSHIP_PROFILE]->(rp)
            CREATE (rp)-[:PROFILE_TARGET]->(p)
        """)
        conn.execute("""
            MATCH (r:Rule {id: "default_home_privacy"}), (l:Location {id: "home"}), (c:Character {id: "char"})
            CREATE (r)-[:APPLIES_AT]->(l)
            CREATE (r)-[:RULE_FOR_CHARACTER]->(c)
        """)

        print("✅ Default 스키마 초기화 완료")


from src.assets.worlds.default.characters import Char, Player  # noqa: E402
world_instance = DefaultWorld(
    narrator = Char(),
    pc       = Player(),
    chars    = [Char(), Player()],
)
