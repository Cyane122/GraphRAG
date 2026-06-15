# ================================
# src/tools/world_editor/field_types.py
#
# 월드별 필드 타입 분류(외형/성격/기타) JSON을 읽고 저장합니다.
# 각 section(static/personality/info/state)의 key → "appearance"|"personality"|"other"
#
# Functions
#   - read_field_types(world_id: str) -> dict : field_types.json 내용을 반환합니다.
#   - write_field_type(world_id: str, section: str, field: str, field_type: str) -> dict : 단일 필드 타입을 저장합니다.
#   - delete_field_type(world_id: str, section: str, field: str) -> dict : 필드 타입 분류를 삭제(기본값으로 복원)합니다.
# ================================

from __future__ import annotations

import json
from pathlib import Path

from src.tools.world_editor.worlds import world_pkg_dir

_FILE_NAME = "field_types.json"
_VALID_TYPES = frozenset({"appearance", "personality", "other"})
_VALID_SECTIONS = frozenset({"static", "personality", "info", "state"})


def _field_types_path(world_id: str) -> Path:
    """월드 패키지 안의 field_types.json 경로를 반환합니다."""
    return world_pkg_dir(world_id) / _FILE_NAME


def read_field_types(world_id: str) -> dict:
    """world_id의 field_types.json 내용을 반환합니다. 파일이 없으면 빈 dict."""
    path = _field_types_path(world_id)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def write_field_type(world_id: str, section: str, field: str, field_type: str) -> dict:
    """단일 필드의 타입 분류를 저장합니다.

    section ∈ {static, personality, info, state}
    field_type ∈ {appearance, personality, other}
    기존 파일의 나머지 항목은 유지됩니다.
    """
    if section not in _VALID_SECTIONS:
        raise ValueError(f"section은 {sorted(_VALID_SECTIONS)} 중 하나여야 합니다: {section!r}")
    if field_type not in _VALID_TYPES:
        raise ValueError(f"field_type은 {sorted(_VALID_TYPES)} 중 하나여야 합니다: {field_type!r}")
    if not field or not isinstance(field, str):
        raise ValueError("field는 비어 있지 않은 문자열이어야 합니다.")

    data = read_field_types(world_id)
    data.setdefault(section, {})[field] = field_type
    _save(world_id, data)
    return {"ok": True, "section": section, "field": field, "type": field_type}


def delete_field_type(world_id: str, section: str, field: str) -> dict:
    """필드 타입 분류를 삭제합니다 (기본 섹션 타입으로 복원됩니다)."""
    data = read_field_types(world_id)
    removed = data.get(section, {}).pop(field, None)
    if removed is not None:
        if not data[section]:
            del data[section]
        _save(world_id, data)
    return {"ok": True, "removed": removed is not None, "section": section, "field": field}


def _save(world_id: str, data: dict) -> None:
    """field_types.json을 atomic하게 저장합니다."""
    path = _field_types_path(world_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
