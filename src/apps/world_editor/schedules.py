# ================================
# src/apps/world_editor/schedules.py
#
# World Editor 전용 전역/시나리오 schedule 템플릿 JSON을 읽고 저장합니다.
#
# Functions
#   - read_schedule_templates(world_id: str) -> dict : schedule_templates.json 내용을 반환합니다.
#   - write_schedule_templates(world_id: str, data: dict) -> dict : schedule_templates.json을 검증 후 저장합니다.
# ================================

from __future__ import annotations

import json
from pathlib import Path

from src.apps.world_editor import source_edit as se
from src.apps.world_editor.worlds import world_pkg_dir

_FILE_NAME = "schedule_templates.json"


def _schedule_path(world_id: str) -> Path:
    """월드 패키지 안의 schedule template JSON 경로를 반환합니다."""
    return world_pkg_dir(world_id) / _FILE_NAME


def _empty_templates() -> dict:
    """빈 schedule template 문서 기본값을 반환합니다."""
    return {
        "world": [],
        "scenarios": {},
        "note": "Applied at runtime: each entry needs owner_id + insert_schedule fields (recurrence/day_of_weeks/start_time/end_time/location_id).",
    }


def _validate_entry(entry: object, label: str) -> dict:
    """단일 schedule template entry를 검증하고 정규화합니다."""
    if not isinstance(entry, dict):
        raise ValueError(f"{label} 항목은 dict 여야 합니다.")
    if not isinstance(entry.get("id"), str) or not entry.get("id"):
        raise ValueError(f"{label} 항목에는 id 문자열이 필요합니다.")
    if not isinstance(entry.get("name", ""), str):
        raise ValueError(f"{label} name 은 문자열이어야 합니다.")
    if not isinstance(entry.get("summary", ""), str):
        raise ValueError(f"{label} summary 는 문자열이어야 합니다.")
    if not isinstance(entry.get("tags", []), list):
        raise ValueError(f"{label} tags 는 list 여야 합니다.")
    return dict(entry)


def _validate_templates(data: dict) -> dict:
    """schedule template 문서 전체를 검증하고 저장 가능한 dict로 반환합니다."""
    if not isinstance(data, dict):
        raise ValueError("schedule templates 는 dict 여야 합니다.")
    world_entries = data.get("world", [])
    scenarios = data.get("scenarios", {})
    if not isinstance(world_entries, list):
        raise ValueError("world 는 schedule list 여야 합니다.")
    if not isinstance(scenarios, dict):
        raise ValueError("scenarios 는 scenario_id→schedule list dict 여야 합니다.")
    normalized = {
        "world": [_validate_entry(item, "world") for item in world_entries],
        "scenarios": {},
        "note": str(data.get("note") or "Applied at runtime: each entry needs owner_id + insert_schedule fields (recurrence/day_of_weeks/start_time/end_time/location_id)."),
    }
    for scenario_id, entries in scenarios.items():
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("scenario_id 는 빈 문자열이 아닌 str 이어야 합니다.")
        if not isinstance(entries, list):
            raise ValueError(f"scenarios[{scenario_id!r}] 는 schedule list 여야 합니다.")
        normalized["scenarios"][scenario_id] = [
            _validate_entry(item, f"scenarios[{scenario_id!r}]")
            for item in entries
        ]
    return normalized


def read_schedule_templates(world_id: str) -> dict:
    """전역/시나리오 schedule template JSON을 반환합니다."""
    path = _schedule_path(world_id)
    if not path.exists():
        return _empty_templates()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{_FILE_NAME} JSON 파싱 실패: {e}") from e
    return _validate_templates(data)


def write_schedule_templates(world_id: str, data: dict) -> dict:
    """전역/시나리오 schedule template JSON을 저장합니다."""
    normalized = _validate_templates(data)
    path = _schedule_path(world_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    try:
        old_text = path.read_text(encoding="utf-8") if path.exists() else ""
        if old_text == new_text:
            return {"ok": True, "message": "변경할 schedule template 이 없습니다.", "backup": None, "formatted": False}
        backup = se._safe_write(path, new_text) if path.exists() else None
        if not path.exists():
            path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return {"ok": True, "message": "schedule template 을 저장했습니다.", "backup": backup, "formatted": True}
