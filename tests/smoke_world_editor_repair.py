# ================================
# tests/smoke_world_editor_repair.py
#
# World Editor repair and schedule-template API smoke checks.
#
# Functions
#   - main() -> None : 임시 fixture world로 repair preview/apply와 schedule API를 검증합니다.
# ================================

from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from typing import Callable

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.world_editor import repair, schedules, source_edit
from src.apps.world_editor.app import create_app


def _write_fixture(root: Path) -> None:
    """repair smoke fixture 파일을 임시 world 루트에 작성합니다."""
    (root / "characters").mkdir(parents=True)
    (root / "schema.py").write_text(
        """_LOCATIONS = [
    ("loc_a", "A Room", "Short description"),
]

_RULES = [
    ("rule_a", "Rule A", "Summary"),
]
""",
        encoding="utf-8",
    )
    (root / "characters.py").write_text(
        """class FixtureCharacter:
    id = "char_a"

    def build_schema(self, conn):
        static_props = {"birth_year": 2000, "nationality": "Korean"}
        insert_static_inline(
            conn,
            self.id,
            "HAS_PROFILE",
            "StaticProfile",
            f"{self.id}_static",
            **static_props,
        )
        schedule_kwargs = {"name": "Morning", "summary": "Wake up"}
        insert_schedule(
            conn,
            self.id,
            f"{self.id}_morning",
            **schedule_kwargs,
        )


class SharedScheduleCharacter:
    id = "char_shared"

    def build_schema(self, conn):
        for owner_id in ["char_shared", "other"]:
            insert_schedule(
                conn,
                owner_id,
                "char_shared_shared",
                name="Shared",
            )
""",
        encoding="utf-8",
    )


def _with_fake_world(root: Path) -> Callable[[], None]:
    """world_pkg_dir 참조를 임시 fixture 루트로 바꾸고 복구 함수를 반환합니다."""
    old_repair_world_pkg_dir = repair.world_pkg_dir
    old_source_world_pkg_dir = source_edit.world_pkg_dir
    old_schedules_world_pkg_dir = schedules.world_pkg_dir
    old_compile_world_graph = repair.compiler.compile_world_graph
    old_scenario_ids = repair.migrate._scenario_ids
    repair.world_pkg_dir = lambda _world_id: root
    source_edit.world_pkg_dir = lambda _world_id: root
    schedules.world_pkg_dir = lambda _world_id: root
    repair.compiler.compile_world_graph = lambda *_args, **_kwargs: {}
    repair.migrate._scenario_ids = lambda _world_id: [None]

    def _restore() -> None:
        """monkeypatch한 world_pkg_dir 참조를 원복합니다."""
        repair.world_pkg_dir = old_repair_world_pkg_dir
        source_edit.world_pkg_dir = old_source_world_pkg_dir
        schedules.world_pkg_dir = old_schedules_world_pkg_dir
        repair.compiler.compile_world_graph = old_compile_world_graph
        repair.migrate._scenario_ids = old_scenario_ids

    return _restore


def _assert_ok(result: dict, label: str) -> None:
    """SaveResult 스타일 dict가 성공인지 확인합니다."""
    if not result.get("ok"):
        raise AssertionError(f"{label} failed: {result}")


def _repair_graph() -> dict:
    """repair report에 넣을 최소 graph fixture를 반환합니다."""
    return {
        "characters": [
            {
                "id": "char_a",
                "static": {"birth_year": 2000, "nationality": "Korean"},
                "edit": {
                    "static": {
                        "editable": False,
                        "reason": "uses computed/spread values; edit in source",
                    }
                },
                "schedules": [
                    {
                        "id": "char_a_morning",
                        "name": "Morning",
                        "summary": "Wake up",
                        "edit": {
                            "editable": False,
                            "reason": "matching insert_schedule call is computed or shared; edit in source",
                        },
                    }
                ],
            },
            {
                "id": "char_shared",
                "edit": {},
                "schedules": [
                    {
                        "id": "char_shared_shared",
                        "name": "Shared",
                        "edit": {
                            "editable": False,
                            "reason": "matching insert_schedule call is computed or shared; edit in source",
                        },
                    }
                ],
            },
        ],
        "locations": [
            {
                "id": "loc_a",
                "editable": False,
                "reason": "non-template shape; edit in source",
            }
        ],
        "rules": [],
        "relationships": [],
    }


def _smoke_repairs() -> None:
    """tuple/blob/schedule repair 후보, preview, apply 동작을 검증합니다."""
    with tempfile.TemporaryDirectory(prefix="we_repair_") as tmp:
        root = Path(tmp)
        _write_fixture(root)
        restore = _with_fake_world(root)
        try:
            graph = _repair_graph()
            report = repair.build_repair_report("fixture", None, graph)
            repairable = {(i["type"], i["target"]): i["repairable"] for i in report["issues"]}
            assert repairable[("non_template_tuple", "loc_a")] is True
            assert repairable[("computed_blob", "char_a")] is True
            assert repairable[("computed_schedule", "char_a:char_a_morning")] is True
            assert repairable[("computed_schedule", "char_shared:char_shared_shared")] is False

            tuple_preview = repair.repair_issue("fixture", None, graph, "non_template_tuple", "location", "loc_a")
            _assert_ok(tuple_preview, "tuple preview")
            assert "prompt_priority" not in tuple_preview["diff"]
            _assert_ok(
                repair.repair_issue("fixture", None, graph, "non_template_tuple", "location", "loc_a", True),
                "tuple apply",
            )
            assert repair._can_repair_tuple("fixture", "location", "loc_a") is False

            blob_preview = repair.repair_issue("fixture", None, graph, "computed_blob", "character.static", "char_a")
            _assert_ok(blob_preview, "blob preview")
            assert "birth_year=2000" in blob_preview["diff"]
            _assert_ok(
                repair.repair_issue("fixture", None, graph, "computed_blob", "character.static", "char_a", True),
                "blob apply",
            )

            schedule_preview = repair.repair_issue(
                "fixture",
                None,
                graph,
                "computed_schedule",
                "schedule",
                "char_a:char_a_morning",
            )
            _assert_ok(schedule_preview, "schedule preview")
            assert "summary='Wake up'" in schedule_preview["diff"]
            _assert_ok(
                repair.repair_issue(
                    "fixture",
                    None,
                    graph,
                    "computed_schedule",
                    "schedule",
                    "char_a:char_a_morning",
                    True,
                ),
                "schedule apply",
            )
        finally:
            restore()


def _smoke_schedule_api() -> None:
    """FastAPI TestClient로 /api/worlds와 schedule_templates GET/PUT을 확인합니다."""
    with tempfile.TemporaryDirectory(prefix="we_schedule_") as tmp:
        root = Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        restore = _with_fake_world(root)
        try:
            client = TestClient(create_app())
            worlds_res = client.get("/api/worlds")
            assert worlds_res.status_code == 200
            get_res = client.get("/api/worlds/fixture/schedule_templates")
            assert get_res.status_code == 200
            assert get_res.json()["data"]["world"] == []
            payload = {
                "data": {
                    "world": [{"id": "global_morning", "name": "Morning", "summary": "Global", "tags": ["daily"]}],
                    "scenarios": {"default": [{"id": "scene_lunch", "name": "Lunch", "summary": "", "tags": []}]},
                    "note": "smoke",
                }
            }
            put_res = client.put("/api/worlds/fixture/schedule_templates", json=payload)
            assert put_res.status_code == 200
            assert put_res.json()["ok"] is True
            get_res = client.get("/api/worlds/fixture/schedule_templates")
            assert get_res.json()["data"]["scenarios"]["default"][0]["id"] == "scene_lunch"
        finally:
            restore()


def main() -> None:
    """임시 fixture 기반 World Editor smoke checks를 실행합니다."""
    _smoke_repairs()
    _smoke_schedule_api()
    print("world_editor repair smoke ok")


if __name__ == "__main__":
    main()
