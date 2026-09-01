# ================================
# tests/smoke_world_editor_migrate.py
#
# cfg 패턴 마이그레이션 + cfg-driven build_schema 스모크 체크.
#
# Functions
#   - main() -> None : cfg build_schema 노드 생성과 migrate.analyze 추출을 검증합니다.
# ================================

from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

import kuzu

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.assets.worlds.base import World
from src.assets.worlds.base_character import Character
from src.apps.world_editor import migrate
from src.apps.world_editor import source_edit


class _CfgChar(Character):
    """cfg 패턴 합성 캐릭터 (DEFAULT_CFG + scenario override 병합 검증용)."""

    id = "cfg_char"
    name = "Cfg"
    char_type = "npc"
    DEFAULT_CFG = {
        "static": {"birth_year": 2000, "gender": "female"},
        "personality": {"core_traits": "calm"},
        "info": {"age": 20},
        "state": {"mood": "neutral", "stress_level": 2, "has_menstrual_cycle": True},
    }
    SCENARIO_OVERRIDES = {"alt": {"state": {"mood": "tense", "stress_level": 7}, "info": {"age": 21}}}


def _smoke_cfg_build() -> None:
    """cfg-driven build_schema 가 4-tier 노드를 만들고 override 가 병합되는지 확인합니다."""
    tmp = Path(tempfile.mkdtemp(prefix="we_migrate_smoke_"))
    db = conn = None
    try:
        db = kuzu.Database(str(tmp / "db"))
        conn = kuzu.Connection(db)
        World()._build_tables(conn)
        _CfgChar("alt").build_schema(conn)

        def one(query: str):
            res = conn.execute(query)
            return res.get_next() if res.has_next() else None

        static = one("MATCH (c:Character {id:'cfg_char'})-[:HAS_PROFILE]->(n:StaticProfile) RETURN n.props")
        info = one("MATCH (c:Character {id:'cfg_char'})-[:HAS_INFO]->(n:DynamicInformation) RETURN n.props")
        pers = one("MATCH (c:Character {id:'cfg_char'})-[:HAS_PERSONALITY]->(n:Personality) RETURN n.props")
        state = one("MATCH (c:Character {id:'cfg_char'})-[:HAS_STATE]->(d:DynamicState) RETURN d.mood, d.stress_level, d.id")

        assert static and '"gender": "female"' in static[0], f"static props wrong: {static}"
        assert info and '"age": 21' in info[0], f"info override not merged: {info}"  # 21 = override
        assert pers is not None, "personality node missing"
        assert state == ["tense", 7, "cfg_char_state"], f"state override/id wrong: {state}"
    finally:
        del conn
        del db
        gc.collect()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


_FIXTURE_SOURCE = '''from __future__ import annotations

import kuzu

from src.assets.worlds.base import insert_static_inline
from src.assets.worlds.base_character import Character


class FixtureChar(Character):
    id = "fix_char"
    name = "Fixture"
    char_type = "npc"

    def build_schema(self, conn: kuzu.Connection) -> None:
        conn.execute(
            "CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: $type})",
            {"id": self.id, "name": self.name, "aliases": [], "type": self.char_type},
        )
        insert_static_inline(
            conn, self.id, "HAS_PROFILE", "StaticProfile", f"{self.id}_static",
            birth_year=2000, nationality="Korean",
        )
        if self.scenario_id == "alt":
            _state = {"id": f"{self.id}_state", "mood": "tense", "stress_level": 7}
        else:
            _state = {"id": f"{self.id}_state", "mood": "calm", "stress_level": 1}
        conn.execute("CREATE (:DynamicState {id: $id, mood: $mood, stress_level: $stress_level})", _state)
        conn.execute(
            "MATCH (c:Character {id: $id}), (d:DynamicState {id: $did}) CREATE (c)-[:HAS_STATE]->(d)",
            {"id": self.id, "did": f"{self.id}_state"},
        )
'''


def _smoke_migrate_analyze() -> None:
    """imperative 픽스처에서 DEFAULT_CFG/SCENARIO_OVERRIDES 추출이 맞는지 확인합니다 (재컴파일 없이)."""
    with tempfile.TemporaryDirectory(prefix="we_migrate_fix_") as tmp:
        fixture = Path(tmp) / "fix_char.py"
        fixture.write_text(_FIXTURE_SOURCE, encoding="utf-8")

        old_find = source_edit.find_character_file
        old_scenarios = migrate.scenario_infos
        source_edit.find_character_file = lambda _w, _c: fixture
        migrate.scenario_infos = lambda _w: [{"scenario_id": "default"}, {"scenario_id": "alt"}]
        try:
            result = migrate.analyze_character("fixture", "fix_char")
        finally:
            source_edit.find_character_file = old_find
            migrate.scenario_infos = old_scenarios

        assert result.get("migratable"), f"fixture should be migratable: {result}"
        default = result["default_cfg"]
        overrides = result["scenario_overrides"]
        assert default["static"] == {"birth_year": 2000, "nationality": "Korean"}, default
        assert default["state"] == {"mood": "calm", "stress_level": 1}, default  # else branch, id dropped
        assert overrides == {"alt": {"state": {"mood": "tense", "stress_level": 7}}}, overrides


def main() -> None:
    """cfg build_schema + migrate.analyze 스모크 체크를 실행합니다."""
    _smoke_cfg_build()
    _smoke_migrate_analyze()
    print("world_editor migrate smoke ok")


if __name__ == "__main__":
    main()
