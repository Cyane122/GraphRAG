# ================================
# src/apps/world_editor/source_ops/records.py
#
# Tuple-row and event source creation and deletion operations.
#
# Functions
#   - add_tuple_row(world_id: str, kind: str, values: dict) -> dict : Add a tuple row.
#   - delete_tuple_row(world_id: str, kind: str, row_id: str) -> dict : Delete a tuple row.
#   - add_event(world_id: str, event: dict) -> dict : Add an event.
#   - delete_event(world_id: str, event_id: str) -> dict : Delete an event.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

_KIND_METHOD = {"location": ("_build_locations", "_LOCATIONS"), "rule": ("_build_rule", "_RULES")}


def _row_from_values(kind: str, values: dict, old_row: tuple | None = None) -> tuple:
    """values(컬럼명→값)를 템플릿 컬럼 순서의 튜플로 만듭니다(누락은 old_row 또는 기본값)."""
    columns = se._TUPLE_COLUMNS[kind]
    defaults: dict = {"prompt_priority": 0, "tags": [], "links": [], "scenarios": []}
    row = []
    for i, col in enumerate(columns):
        if col in values:
            row.append(values[col])
        elif old_row is not None:
            row.append(old_row[i])
        else:
            row.append(defaults.get(col, ""))
    return tuple(row)


def add_tuple_row(world_id: str, kind: str, values: dict) -> dict:
    """위치/규칙 행을 _LOCATIONS/_RULES 리스트에 추가합니다(메서드가 없으면 거부 — 손글씨 보호)."""
    if kind not in _KIND_METHOD:
        return se._fail(f"알 수 없는 kind: {kind}")
    method_name, list_name = _KIND_METHOD[kind]
    id_col = se._TUPLE_COLUMNS[kind][0]
    if not values.get(id_col):
        return se._fail(f"{id_col} 가 필요합니다.")

    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = _find_world_class(tree)
    method = se._find_method(cls, method_name) if cls else None
    if method is None:
        return se._fail(f"{method_name} 가 없어 추가할 수 없습니다(손글씨 월드). world_editor로 만든 월드를 쓰거나 소스에서 추가하세요.")

    new_row = _row_from_values(kind, values)

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        m = se._find_method(c, method_name) if c else None
        return _find_list_in_method(m, list_name) if m else None

    def _transform(old: list) -> list:
        return list(old) + [new_row]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"{kind} '{values[id_col]}' 행을 추가했습니다.")


def delete_tuple_row(world_id: str, kind: str, row_id: str) -> dict:
    """위치/규칙 행을 리스트에서 제거합니다."""
    if kind not in _KIND_METHOD:
        return se._fail(f"알 수 없는 kind: {kind}")
    method_name, list_name = _KIND_METHOD[kind]
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        m = se._find_method(c, method_name) if c else None
        return _find_list_in_method(m, list_name) if m else None

    def _transform(old: list) -> list:
        return [row for row in old if not (isinstance(row, (tuple, list)) and row and row[0] == row_id)]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    # 삭제 후 행 수가 줄었는지 확인하기 위해 먼저 존재 검사.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = _locate(tree)
    if node is None:
        return se._fail(f"{kind} 리스트를 찾지 못했습니다.")
    rows = ast.literal_eval(node) if se._is_clean_literal_node(node) else []
    if not any(isinstance(r, (tuple, list)) and r and r[0] == row_id for r in rows):
        return se._fail(f"삭제 대상 행이 없습니다: {row_id}")

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"{kind} '{row_id}' 행을 삭제했습니다.")


def add_event(world_id: str, event: dict) -> dict:
    """_EVENTS 리스트에 이벤트 dict 를 추가합니다(필수 키는 기본값으로 보강)."""
    if not event.get("id"):
        return se._fail("event id 가 필요합니다.")
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = _find_world_class(tree)
    method = se._find_method(cls, "_build_seed_events") if cls else None
    if method is None:
        return se._fail("_build_seed_events 가 없어 추가할 수 없습니다. world_editor로 만든 월드를 쓰거나 소스에서 추가하세요.")

    # 전체 이벤트 dict 구성 — 기본값 + 사용자 값 + _involved/_location_id.
    ev = dict(_EVENT_DEFAULTS)
    ev.update({k: v for k, v in event.items() if k in _EVENT_DEFAULTS or k == "id"})
    ev["id"] = event["id"]
    ev["_involved"] = list(event.get("involved", []) or [])
    ev["_location_id"] = event.get("location_id", "") or ""

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        m = se._find_method(c, "_build_seed_events") if c else None
        return _find_list_in_method(m, "_EVENTS") if m else None

    def _transform(old: list) -> list:
        return list(old) + [ev]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"이벤트 '{event['id']}' 를 추가했습니다.")


def delete_event(world_id: str, event_id: str) -> dict:
    """_EVENTS 리스트에서 id 가 event_id 인 이벤트를 제거합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        m = se._find_method(c, "_build_seed_events") if c else None
        return _find_list_in_method(m, "_EVENTS") if m else None

    def _transform(old: list) -> list:
        return [ev for ev in old if not (isinstance(ev, dict) and ev.get("id") == event_id)]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = _locate(tree)
    if node is None:
        return se._fail("_EVENTS 리스트를 찾지 못했습니다.")
    evs = ast.literal_eval(node) if se._is_clean_literal_node(node) else []
    if not any(isinstance(ev, dict) and ev.get("id") == event_id for ev in evs):
        return se._fail(f"삭제 대상 이벤트가 없습니다: {event_id}")

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"이벤트 '{event_id}' 를 삭제했습니다.")

