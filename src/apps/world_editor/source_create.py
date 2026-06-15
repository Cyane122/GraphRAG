# ================================
# src/apps/world_editor/source_create.py
#
# 엔티티 생성/삭제 + 누락 구조 자동 스캐폴딩. source_edit(편집)의 안전 헬퍼를 재사용합니다.
# 핵심 원칙: clean 리터럴 holder 에는 항목을 추가/삭제(전체 리터럴 재emit→_apply_edit 검증).
# holder 메서드가 '아예 없을' 때만 메서드를 삽입한다(이미 있으면 절대 중복 삽입 안 함 — 손글씨 로직 보호).
#
# Classes
#   - _SchemaSource : schema.py 소스와 AST 분석 결과 묶음.
#
# Functions
#   - register_character(world_id: str, class_name: str, char_id: str, char_type: str) -> dict
#   - add_relationship(world_id: str, source: str, target: str, rel_type, affinity, trust, current_status) -> dict
#   - delete_relationship(world_id: str, source: str, target: str) -> dict
#   - add_tuple_row(world_id: str, kind: str, values: dict) -> dict
#   - delete_tuple_row(world_id: str, kind: str, row_id: str) -> dict
#   - add_event(world_id: str, event: dict) -> dict
#   - delete_event(world_id: str, event_id: str) -> dict
#   - set_blob(world_id: str, char_id: str, role: str, props: dict) -> dict  (upsert)
#   - set_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict
#   - edit_subnode(world_id: str, char_id: str, node_id: str, fields: dict) -> dict  (item/goal/secret 편집)
#   - add_subnode(world_id: str, char_id: str, kind: str, fields: dict) -> dict       (item/goal/secret 추가)
#   - add_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict (스케줄 추가)
#   - set_aliases(world_id: str, char_id: str, aliases: list[str]) -> dict     (캐릭터 별명 치환)
#   - list_all_characters(world_id: str) -> list[dict]                        (시나리오 무관 전체 캐릭터)
#   - get_scenario_characters(world_id: str, scenario_id: str | None) -> list[str]
#   - set_scenario_characters(world_id: str, scenario_id: str | None, char_ids: list[str]) -> dict
#   - create_scenario(world_id: str, scenario_id: str, display_name: str) -> dict
#   - update_scenario_meta(world_id: str, scenario_id: str, display_name: str) -> dict
#   - rename_scenario(world_id: str, old_scenario_id: str, new_scenario_id: str) -> dict
#   - update_scene_types(world_id: str, scene_types: dict[str, str], scenario_id: str | None = None) -> dict
#   - update_default_perspective(world_id: str, perspective: object, scenario_id: str | None = None) -> dict
#   - add_extra_slot(world_id: str, slot_id: str, label: str, sub: str) -> dict
#   - delete_extra_slot(world_id: str, slot_id: str) -> dict
# ================================

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from src.apps.world_editor import source_edit as se
from src.apps.world_editor.worlds import world_pkg_dir

_SID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 이벤트/튜플 기본값 — 생성 시 누락 키를 채운다.
_EVENT_DEFAULTS: dict = {
    "summary": "", "timestamp": "2024-01-01T09:00:00", "importance": 5,
    "impact": "", "memory_type": "episodic", "decay_rate": 0.15,
    "narrative_summary": "", "state_summary": "", "summary_level": 0,
}


@dataclass(frozen=True)
class _SchemaSource:
    """schema.py 소스와 AST 분석 결과를 함께 보관합니다."""

    path: Path
    text: str
    tree: ast.Module
    line_offsets: list[int]


def _load_schema_source(world_id: str) -> _SchemaSource | dict:
    """월드 schema.py를 읽고 AST로 파싱한 결과를 반환합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    return _SchemaSource(path=path, text=text, tree=ast.parse(text), line_offsets=se._line_offsets(text))


def _write_schema_source(source: _SchemaSource, new_text: str, message: str) -> dict:
    """schema.py 새 소스를 parse 검증한 뒤 안전하게 기록합니다."""
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(source.path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(message, backup)


def _replace_class_attr_literal(source: _SchemaSource, cls: ast.ClassDef, attr: str, value: object) -> str | dict:
    """World 클래스 속성 clean literal 값을 새 literal 소스로 치환합니다."""
    node = _find_class_attr(cls, attr)
    if node is None or not se._is_clean_literal_node(node):
        return se._fail(f"{attr} clean 리터럴을 찾지 못했습니다. 소스에서 직접 편집하세요.")
    if attr == "SCENE_TYPES" and not isinstance(node, ast.Dict):
        return se._fail("SCENE_TYPES clean 리터럴 dict 를 찾지 못했습니다. 소스에서 직접 편집하세요.")
    start, end = se._node_span(source.text, node, source.line_offsets)
    base_indent = se._base_indent(source.text, node, source.line_offsets)
    return se._replace_node_span(source.text, start, end, se._emit(value, base_indent))


def _replace_scenario_keyword(source: _SchemaSource, scenario_id: str, key: str, value: object) -> str | dict:
    """Scenario(...) keyword 값을 치환하거나 삽입한 schema.py 소스를 반환합니다."""
    call = _find_scenario_call(source.tree, scenario_id)
    if call is None:
        return se._fail(f"시나리오를 찾지 못했습니다: {scenario_id}")
    return _replace_or_insert_call_keyword(source.text, call, key, value)


def _replace_scenario_world_keyword(source: _SchemaSource, scenario_id: str, key: str, value: object) -> str | dict:
    """Scenario(..., world=World(...)) 안의 World(...) keyword를 치환하거나 삽입합니다."""
    world_call = _find_scenario_world_call(source.tree, scenario_id)
    if world_call is None:
        return se._fail(f"시나리오 World(...) 호출을 찾지 못했습니다: {scenario_id}")
    return _replace_or_insert_call_keyword(source.text, world_call, key, value)


# ──────────────────────────────────────────────────────────────────────
# 공통: clean 리터럴 노드 1개를 통째로 변형해 재기록
# ──────────────────────────────────────────────────────────────────────


def _rewrite_literal(path: Path, locate, transform, relocate, message: str) -> dict:
    """파일에서 locate(tree)로 찾은 리터럴 노드를 transform(old)→새 값으로 통째 치환합니다.

    locate: tree -> ast 노드(list/dict) | None. transform: 파이썬값 -> 새 파이썬값.
    relocate: new_tree -> literal_eval 된 값(검증용). 모든 안전 절차는 se._apply_edit 가 수행.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    node = locate(tree)
    if node is None:
        return se._fail("대상 리터럴을 찾지 못했습니다.")
    if not se._is_clean_literal_node(node):
        return se._fail("대상이 clean 리터럴이 아닙니다(손글씨 구조). 소스에서 직접 편집하세요.")

    start, end = se._node_span(text, node, line_offsets)
    old_value = ast.literal_eval(text[start:end])
    new_value = transform(old_value)
    base_indent = se._base_indent(text, node, line_offsets)
    new_src = se._emit(new_value, base_indent)

    return se._apply_edit(path, text, new_src, start, end,
                          expected=new_value, relocate=relocate, message=message)


def _find_world_class(tree: ast.Module) -> ast.ClassDef | None:
    """schema.py 의 World 서브클래스(= build_schema 메서드를 가진 클래스)를 찾습니다."""
    for cls in se._iter_classes(tree):
        if se._find_method(cls, "build_schema") is not None:
            return cls
    return None


def _find_class_attr(cls: ast.ClassDef, attr: str) -> ast.expr | None:
    """클래스 body 직속 attr 할당값을 반환합니다."""
    for stmt in cls.body:
        names, value = se._assign_target_names(stmt)
        if attr in names:
            return value
    return None


def _is_self_attr_target(target: ast.expr, attr: str) -> bool:
    """AST target 이 self.<attr> 대입 대상인지 반환합니다."""
    return (
        isinstance(target, ast.Attribute)
        and target.attr == attr
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def _find_init_self_attr_stmt(cls: ast.ClassDef, attr: str) -> ast.stmt | None:
    """__init__ body 안의 self.<attr> 대입 문장을 반환합니다."""
    init = se._find_method(cls, "__init__")
    if init is None:
        return None
    for stmt in init.body:
        if isinstance(stmt, ast.Assign) and any(_is_self_attr_target(target, attr) for target in stmt.targets):
            return stmt
        if isinstance(stmt, ast.AnnAssign) and _is_self_attr_target(stmt.target, attr):
            return stmt
    return None


def _remove_statement(text: str, stmt: ast.stmt, line_offsets: list[int]) -> str:
    """AST statement 전체 줄을 소스에서 제거한 새 문자열을 반환합니다."""
    start, end = se._node_span(text, stmt, line_offsets)
    if text[end:end + 2] == "\r\n":
        end += 2
    elif end < len(text) and text[end] in "\r\n":
        end += 1
    return text[:start] + text[end:]


def _remove_init_scene_types_override(text: str) -> str | dict:
    """과거 템플릿의 __init__ self.SCENE_TYPES 대입을 제거합니다."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return se._fail(f"SCENE_TYPES 갱신 결과가 파싱되지 않습니다: {exc}")
    cls = _find_world_class(tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")
    stmt = _find_init_self_attr_stmt(cls, "SCENE_TYPES")
    if stmt is None:
        return text
    return _remove_statement(text, stmt, se._line_offsets(text))


def _find_list_in_method(method: ast.FunctionDef, prefer: str) -> ast.List | None:
    """메서드 body 직속의 list 리터럴 할당값을 찾습니다(이름이 prefer면 우선)."""
    fallback: ast.List | None = None
    for stmt in method.body:
        names, value = se._assign_target_names(stmt)
        if isinstance(value, ast.List):
            if prefer in names:
                return value
            if fallback is None:
                fallback = value
    return fallback


# ──────────────────────────────────────────────────────────────────────
# 관계 (build_relationship 의 _RELS dict)
# ──────────────────────────────────────────────────────────────────────


def add_relationship(world_id: str, source: str, target: str,
                     rel_type, affinity, trust, current_status) -> dict:
    """(source→target) 관계를 upsert 합니다. 이미 있으면 편집, 없으면 _RELS 에 추가."""
    path = se.find_character_file(world_id, source)
    if path is None:
        return se._fail(f"source 캐릭터 파일을 찾지 못했습니다: {source}")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = se._find_character_class(tree, source)
    method = se._find_method(cls, "build_relationship") if cls else None
    if method is None:
        return se._fail("build_relationship 이 없습니다. 새 캐릭터는 world_editor로 생성하세요.")

    dicts = se._find_rel_dicts(method)
    node, _reason = se._rel_value_node_for(dicts, target)
    if node is not None:
        # 이미 존재 → 기존 편집 경로 재사용(부분 갱신 가능).
        return se.edit_relationship(world_id, source, target, rel_type, affinity, trust, current_status)

    # 없음 → clean 리터럴 _RELS dict 에 새 키 추가.
    new_tuple = (rel_type or "acquaintance", affinity or 0, trust or 0, current_status or "")

    def _locate(t: ast.Module):
        c = se._find_character_class(t, source)
        m = se._find_method(c, "build_relationship") if c else None
        if m is None:
            return None
        # _RELS 우선, 없으면 첫 dict 리터럴 할당.
        prefer = None
        fallback = None
        for stmt in m.body:
            names, value = se._assign_target_names(stmt)
            if isinstance(value, ast.Dict):
                if "_RELS" in names:
                    prefer = value
                elif fallback is None:
                    fallback = value
        return prefer or fallback

    def _transform(old: dict) -> dict:
        new = dict(old)
        new[target] = new_tuple
        return new

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"{source}→{target} 관계를 추가했습니다.")


def delete_relationship(world_id: str, source: str, target: str) -> dict:
    """(source→target) 관계 항목을 _RELS dict 에서 제거합니다."""
    path = se.find_character_file(world_id, source)
    if path is None:
        return se._fail(f"source 캐릭터 파일을 찾지 못했습니다: {source}")

    def _locate(t: ast.Module):
        c = se._find_character_class(t, source)
        m = se._find_method(c, "build_relationship") if c else None
        if m is None:
            return None
        for stmt in m.body:
            names, value = se._assign_target_names(stmt)
            if isinstance(value, ast.Dict):
                for k in value.keys:
                    if isinstance(k, ast.Constant) and k.value == target:
                        return value
        return None

    def _transform(old: dict) -> dict:
        new = dict(old)
        new.pop(target, None)
        return new

    def _relocate(t: ast.Module):
        n = _locate(t)
        # 삭제 후엔 _locate 가 None(키 사라짐) → 그땐 빈/갱신 dict 를 따로 찾기 어려우므로
        # 단순히 성공으로 간주하기 위해 expected 와 동일한 값을 만들어 비교한다.
        return _MISS_OK

    # 삭제는 relocate 로 '키 없음'을 검증하기 까다로워 별도 처리: 직접 수행.
    return _delete_key_from_dict(path, _locate, target, f"{source}→{target} 관계를 삭제했습니다.")


_MISS_OK = object()


def _delete_key_from_dict(path: Path, locate, key: str, message: str) -> dict:
    """locate 로 찾은 dict 리터럴에서 key 를 제거하고, 제거됐는지 검증 후 기록합니다."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    node = locate(tree)
    if node is None:
        return se._fail("대상 항목을 찾지 못했습니다.")
    if not se._is_clean_literal_node(node):
        return se._fail("대상이 clean 리터럴이 아닙니다.")
    start, end = se._node_span(text, node, line_offsets)
    old = ast.literal_eval(text[start:end])
    if key not in old:
        return se._fail("삭제 대상 키가 없습니다.")
    new = {k: v for k, v in old.items() if k != key}
    base_indent = se._base_indent(text, node, line_offsets)
    new_src = se._emit(new, base_indent)
    new_text = se._replace_node_span(text, start, end, new_src)
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"치환 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(message, backup)


# ──────────────────────────────────────────────────────────────────────
# 위치 / 규칙 (schema.py 의 _build_locations/_build_rule 안 리스트 리터럴)
# ──────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────
# 이벤트 (_build_seed_events 의 _EVENTS 리스트[dict])
# ──────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────
# blob / state upsert (없으면 build_schema 에 삽입)
# ──────────────────────────────────────────────────────────────────────


def _get_world_extra_slot(world_id: str, role: str) -> dict | None:
    """월드 EXTRA_SLOTS 에서 id==role 인 슬롯을 반환합니다. 없으면 None."""
    from src.apps.world_editor.worlds import load_world
    try:
        world, _ = load_world(world_id, None)
        for slot in (getattr(world, "EXTRA_SLOTS", None) or []):
            if isinstance(slot, dict) and slot.get("id") == role:
                return slot
    except Exception:
        pass
    return None


def set_blob(world_id: str, char_id: str, role: str, props: dict) -> dict:
    """blob 을 upsert 합니다. 이미 리터럴 호출이 있으면 편집, 없으면 build_schema 에 삽입."""
    if role in se._ROLE_LABEL:
        label = se._ROLE_LABEL[role]
        custom_rel = None
    else:
        slot = _get_world_extra_slot(world_id, role)
        if slot is None:
            return se._fail(f"알 수 없는 role: {role}. 세계관의 EXTRA_SLOTS 에도 없습니다.")
        label = slot["label"]
        custom_rel = f"HAS_{label.upper()}"
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = se._find_character_class(tree, char_id)
    if cls is None:
        return se._fail(f"캐릭터 클래스를 찾지 못했습니다: {char_id}")
    method = se._find_method(cls, "build_schema")
    if method is None:
        # build_schema 자체가 없으면 최소 골격(Character 노드 생성)을 먼저 추가한다.
        ens = _ensure_build_schema(path, char_id)
        if not ens.get("ok"):
            return ens
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = se._find_character_class(tree, char_id)
        method = se._find_method(cls, "build_schema")
    call, reason = se._find_blob_call(method, label)
    if call is not None:
        # 리터럴 호출 존재 → 편집 경로.
        return se.edit_blob(world_id, char_id, role, props, _label=(label if custom_rel else None))
    # **splat 등 비리터럴 호출이 이미 있으면 안전하게 거부(손글씨 보호).
    if reason and reason.startswith("uses computed"):
        return se._fail(f"편집 불가: {reason}")
    # 호출 자체가 없음 → build_schema 에 insert_static_inline 호출 삽입.
    return _insert_blob_call(path, char_id, role, label, props, rel=custom_rel)


def _insert_blob_call(path: Path, char_id: str, role: str, label: str, props: dict, rel: str | None = None) -> dict:
    """build_schema 끝에 insert_static_inline(...) 리터럴 호출을 삽입합니다.

    rel 이 None 이면 표준 역할(static/personality/info) rel_map 을 사용합니다.
    커스텀 슬롯은 rel 을 직접 전달해야 합니다.
    """
    if rel is None:
        rel_map = {"static": "HAS_PROFILE", "personality": "HAS_PERSONALITY", "info": "HAS_INFO"}
        if role not in rel_map:
            return se._fail(f"rel 이 지정되지 않았고 표준 role 도 아닙니다: {role}")
        rel = rel_map[role]
    suffix = role  # node_id suffix 는 role(또는 slot_id)을 그대로 사용

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None or not method.body:
        return se._fail("build_schema 본문을 찾지 못했습니다.")

    # 본문 마지막 문장 뒤에 삽입. 들여쓰기는 본문 첫 문장 기준.
    body_indent = " " * method.body[0].col_offset
    inner = body_indent + "    "
    lines = [f"{body_indent}insert_static_inline("]
    lines.append(f'{inner}conn, self.id, "{rel}", "{label}", f"{{self.id}}_{suffix}",')
    for k, v in props.items():
        lines.append(f"{inner}{k}={se._emit(v, inner)},")
    lines.append(f"{body_indent})")
    snippet = "\n" + "\n".join(lines)

    last = method.body[-1]
    _, end = se._node_span(text, last, line_offsets)
    new_text = text[:end] + snippet + text[end:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"삽입 결과가 파싱되지 않습니다: {e}")
    new_text = _ensure_base_import(new_text, "insert_static_inline")
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"import 보강 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"{char_id} 의 {role} blob 을 생성했습니다.", backup)


# ── build_schema / _state 자동 생성 (없을 때만 — 있으면 절대 안 건드림) ──

# Character 노드만 만드는 최소 build_schema (def 는 컬럼0 기준 — _insert_method_in_class 가 들여쓴다).
_BUILD_SCHEMA_SRC = (
    "def build_schema(self, conn: kuzu.Connection) -> None:\n"
    '    """캐릭터 노드를 생성합니다. (world_editor 가 추가)"""\n'
    "    conn.execute(\n"
    '        "CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: $type})",\n'
    '        {"id": self.id, "name": self.name, "aliases": self.aliases, "type": self.char_type},\n'
    "    )\n"
)


def _insert_method_in_class(path: Path, char_id: str, method_col0_src: str, verify_name: str) -> dict:
    """char_id 클래스 body 끝에 메서드를 들여쓰기해 삽입합니다(이미 있으면 거부 — shadow 방지)."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    cls = se._find_character_class(tree, char_id)
    if cls is None or not cls.body:
        return se._fail("캐릭터 클래스를 찾지 못했습니다.")
    if se._find_method(cls, verify_name) is not None:
        return se._fail(f"{verify_name} 이미 존재 — 중복 삽입 방지.")
    # 클래스 body 들여쓰기 = 첫 body 문장의 col_offset. 메서드 소스 각 줄에 그만큼 prefix.
    body_indent = " " * cls.body[0].col_offset
    indented = "\n".join((body_indent + ln if ln else ln) for ln in method_col0_src.splitlines())
    last = cls.body[-1]
    _, end = se._node_span(text, last, line_offsets)
    new_text = text[:end] + "\n\n" + indented + text[end:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"메서드 삽입 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"{verify_name} 메서드를 추가했습니다.", backup)


def _ensure_build_schema(path: Path, char_id: str) -> dict:
    """build_schema 가 없으면 Character 노드만 만드는 최소 build_schema 를 삽입합니다."""
    return _insert_method_in_class(path, char_id, _BUILD_SCHEMA_SRC, "build_schema")


def _insert_state_block(path: Path, char_id: str, fields: dict) -> dict:
    """build_schema 끝에 _state dict + insert_state 호출을 삽입합니다.

    _state 리터럴은 이후 edit_state 가 다시 찾을 수 있도록 유지합니다.
    """
    fields = se.normalize_state_fields(fields)
    if "id" not in fields:
        fields = {"id": f"{char_id}_state", **fields}
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None or not method.body:
        return se._fail("build_schema 본문을 찾지 못했습니다.")
    body_indent = " " * method.body[0].col_offset
    state_src = se._emit(fields, body_indent)
    lines = [
        f"{body_indent}_state = {state_src}",
        f"{body_indent}insert_state(conn, self.id, **_state)",
    ]
    snippet = "\n" + "\n".join(lines)
    last = method.body[-1]
    _, end = se._node_span(text, last, line_offsets)
    new_text = text[:end] + snippet + text[end:]
    new_text = _ensure_base_import(new_text, "insert_state")
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"_state 삽입 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"{char_id} 의 _state 를 생성했습니다.", backup)


def _find_subnode_dict(method: ast.FunctionDef, node_id: str) -> ast.Dict | None:
    """build_schema 내 clean 리터럴 dict 중 ['id'] == node_id 인 노드를 찾습니다.

    Item/Goal/Secret 은 conn.execute("CREATE (:X {...$id...})", {파이썬 리터럴 dict}) 형태라
    그 params dict 가 편집 대상. Character 노드({"id": self.id})·f-string _state 등은
    'id' 가 리터럴이 아니어서 자연히 제외된다.
    """
    for node in ast.walk(method):
        if isinstance(node, ast.Dict) and se._is_clean_literal_node(node):
            try:
                value = ast.literal_eval(node)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict) and value.get("id") == node_id:
                return node
    return None


def edit_subnode(world_id: str, char_id: str, node_id: str, fields: dict) -> dict:
    """캐릭터의 item/goal/secret 노드(파라미터 dict, id로 식별)의 필드를 병합 편집합니다."""
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return se._fail("fields 는 str 키를 가진 dict 여야 합니다.")
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    def _locate(tree: ast.Module):
        cls = se._find_character_class(tree, char_id)
        m = se._find_method(cls, "build_schema") if cls else None
        return _find_subnode_dict(m, node_id) if m else None

    def _transform(old: dict) -> dict:
        new = dict(old)
        new.update(fields)
        new["id"] = node_id  # id 는 식별자라 보존
        return new

    def _relocate(tree: ast.Module):
        n = _locate(tree)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"{char_id} 의 '{node_id}' 노드를 갱신했습니다.")


# Item/Goal/Secret 노드 스키마 (base.py 테이블 컬럼과 정합).
_SUBNODE_SPEC: dict[str, dict] = {
    "item": {
        "label": "Item", "edge": "OWNS", "alias": "i",
        "columns": ("id", "name", "description", "owner_id", "location_id", "emotional_weight", "visibility", "last_seen_at"),
        "defaults": {"name": "", "description": "", "location_id": "", "emotional_weight": 0, "visibility": "private", "last_seen_at": ""},
    },
    "goal": {
        "label": "Goal", "edge": "PURSUES", "alias": "g",
        "columns": ("id", "owner_id", "title", "description", "status", "progress", "subtlety", "next_hint", "trigger_conditions", "completion_conditions", "last_progressed_at"),
        "defaults": {"title": "", "description": "", "status": "active", "progress": 0, "subtlety": 5, "next_hint": "", "trigger_conditions": "", "completion_conditions": "", "last_progressed_at": ""},
    },
    "secret": {
        "label": "Secret", "edge": "HAS_SECRET", "alias": "s",
        "columns": ("id", "owner_id", "title", "private_summary", "public_hint", "status", "sensitivity", "reveal_conditions", "current_reveal_level", "last_hinted_at"),
        "defaults": {"title": "", "private_summary": "", "public_hint": "", "status": "hidden", "sensitivity": 5, "reveal_conditions": "", "current_reveal_level": 0, "last_hinted_at": ""},
    },
}
_SUBNODE_INT_COLS = frozenset({"emotional_weight", "progress", "subtlety", "sensitivity", "current_reveal_level"})


def add_subnode(world_id: str, char_id: str, kind: str, fields: dict) -> dict:
    """캐릭터 build_schema 끝에 새 Item/Goal/Secret 노드 + 소유 엣지(conn.execute 2개)를 삽입합니다."""
    spec = _SUBNODE_SPEC.get(kind)
    if spec is None:
        return se._fail("kind 는 item/goal/secret 중 하나여야 합니다.")
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return se._fail("fields 는 str 키를 가진 dict 여야 합니다.")
    node_id = str(fields.get("id") or "").strip()
    if not node_id:
        return se._fail("노드 id 가 필요합니다.")

    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return se._fail("build_schema 메서드를 찾지 못했습니다.")
    if _find_subnode_dict(method, node_id) is not None:
        return se._fail(f"이미 존재하는 노드 id 입니다: {node_id}")

    # 파라미터 dict: defaults + 허용 컬럼 fields, id/owner_id 강제. int 컬럼은 정수화.
    params = dict(spec["defaults"])
    for key, value in fields.items():
        if key in spec["columns"]:
            params[key] = value
    params["id"] = node_id
    params["owner_id"] = char_id
    for col in spec["columns"]:
        if col in _SUBNODE_INT_COLS:
            try:
                params[col] = int(params.get(col, 0) or 0)
            except (TypeError, ValueError):
                params[col] = 0
    ordered = {col: params.get(col, "") for col in spec["columns"]}

    cols_sql = ", ".join(f"{col}: ${col}" for col in spec["columns"])
    param_src = se._emit(ordered, "            ")
    alias, label, edge = spec["alias"], spec["label"], spec["edge"]
    block = (
        "\n"
        "        conn.execute(\n"
        f'            "CREATE (:{label} {{{cols_sql}}})",\n'
        f"            {param_src},\n"
        "        )\n"
        "        conn.execute(\n"
        f'            "MATCH (c:Character {{id: $cid}}), ({alias}:{label} {{id: $xid}}) CREATE (c)-[:{edge}]->({alias})",\n'
        f'            {{"cid": self.id, "xid": {node_id!r}}},\n'
        "        )\n"
    )
    lines = text.splitlines(keepends=True)
    insert_at = method.body[-1].end_lineno  # 마지막 본문 문장 끝줄(1-indexed) 다음에 삽입
    new_text = "".join(lines[:insert_at] + [block] + lines[insert_at:])
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        return se._fail(f"삽입 결과가 파싱되지 않습니다: {exc}")
    backup = se._safe_write(path, new_text)
    return se._ok(f"{char_id} 에 {kind} '{node_id}' 를 추가했습니다.", backup)


def _ensure_base_import(text: str, name: str) -> str:
    """src.assets.worlds.base 에서 name 을 import 하지 않으면 import 줄을 추가합니다."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
            return text
    last_import_line = 0
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            last_import_line = stmt.end_lineno or last_import_line
    lines = text.splitlines(keepends=True)
    import_line = f"from src.assets.worlds.base import {name}\n"
    return "".join(lines[:last_import_line] + [import_line] + lines[last_import_line:])


def add_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict:
    """캐릭터 build_schema 끝에 새 insert_schedule 호출을 삽입합니다 (필요 시 import 추가)."""
    schedule_id = str(schedule_id or "").strip()
    if not schedule_id:
        return se._fail("schedule_id 가 필요합니다.")
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return se._fail("fields 는 str 키를 가진 dict 여야 합니다.")
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return se._fail("build_schema 메서드를 찾지 못했습니다.")
    existing, _ = se._find_schedule_call(method, char_id, schedule_id)
    if existing is not None:
        return se._fail(f"이미 존재하는 schedule_id 입니다: {schedule_id}")

    inner = "            "
    parts = ["conn", "owner_id=self.id", f"schedule_id={schedule_id!r}"]
    for key in se._SCHEDULE_REWRITE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "prompt_priority":
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
        elif key == "day_of_weeks":
            value = sorted(se._coerce_weekday_set(value))
        elif key == "tags":
            value = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        parts.append(f"{key}={se._emit(value, inner)}")
    block = "\n        insert_schedule(\n" + "".join(f"{inner}{p},\n" for p in parts) + "        )\n"

    lines = text.splitlines(keepends=True)
    insert_at = method.body[-1].end_lineno
    new_text = "".join(lines[:insert_at] + [block] + lines[insert_at:])
    new_text = _ensure_base_import(new_text, "insert_schedule")
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        return se._fail(f"삽입 결과가 파싱되지 않습니다: {exc}")
    backup = se._safe_write(path, new_text)
    return se._ok(f"{char_id} 에 schedule '{schedule_id}' 를 추가했습니다.", backup)


def _find_class_attr_list(cls: ast.ClassDef, attr: str) -> ast.List | None:
    """클래스 body 직속의 `attr = [...]` 리스트 리터럴 노드를 찾습니다."""
    for stmt in cls.body:
        names, value = se._assign_target_names(stmt)
        if attr in names and isinstance(value, ast.List):
            return value
    return None


def set_aliases(world_id: str, char_id: str, aliases: list[str]) -> dict:
    """캐릭터 클래스의 `aliases=[...]` 리스트 리터럴을 통째 치환합니다(전체 교체).

    별명은 병합이 아니라 전체 교체다(삭제도 가능해야 하므로). 빈 문자열은 버리고,
    중복은 첫 등장 순서를 유지하며 제거한다. aliases 리터럴이 클래스에 없으면 거부.
    """
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        return se._fail("aliases 는 문자열 리스트여야 합니다.")
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    def _locate(tree: ast.Module):
        cls = se._find_character_class(tree, char_id)
        return _find_class_attr_list(cls, "aliases") if cls else None

    def _transform(_old: list) -> list:
        seen: set[str] = set()
        out: list[str] = []
        for a in aliases:
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out

    def _relocate(tree: ast.Module):
        n = _locate(tree)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate,
                            f"{char_id} 의 aliases 를 갱신했습니다.")


def set_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict:
    """DynamicState 를 upsert 합니다. 정적 scenario_id 분기면 해당 branch 를 편집합니다."""
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return se._fail("fields 는 str 키를 가진 dict 여야 합니다.")
    fields = se.normalize_state_fields(fields)
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = se._find_character_class(tree, char_id)
    if cls is None:
        return se._fail(f"캐릭터 클래스를 찾지 못했습니다: {char_id}")
    method = se._find_method(cls, "build_schema")
    if method is None:
        # build_schema 가 없으면 최소 골격을 먼저 추가한 뒤 _state 를 삽입한다.
        ens = _ensure_build_schema(path, char_id)
        if not ens.get("ok"):
            return ens
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = se._find_character_class(tree, char_id)
        method = se._find_method(cls, "build_schema")

    node, _reason = se._find_state_dict(method, scenario_id)
    if node is not None:
        return se.edit_state(world_id, char_id, fields, scenario_id)

    # _state 가 직속에 없음 — 분기형(if/elif 안)인지, 아예 없는지 구분.
    has_branched = any(
        "_state" in se._assign_target_names(s)[0]
        for s in ast.walk(method) if isinstance(s, (ast.Assign, ast.AnnAssign))
    )
    if has_branched:
        return se._fail("편집 불가: 시나리오 분기형 _state 입니다. 소스에서 편집하세요.")
    # 전무 → 새 _state 블록 삽입.
    return _insert_state_block(path, char_id, fields)


# ──────────────────────────────────────────────────────────────────────
# 캐릭터 등록 (characters/__init__.py + schema.py 수정)
# ──────────────────────────────────────────────────────────────────────


def register_character(world_id: str, class_name: str, char_id: str, char_type: str) -> dict:
    """생성된 캐릭터를 characters/__init__.py 와 schema.py(import + chars 리스트 + narrator/pc)에 등록합니다."""
    pkg = world_pkg_dir(world_id)

    # 1. characters/__init__.py 에 export import 추가(중복 방지).
    init_path = pkg / "characters" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8") if init_path.exists() else '"""캐릭터 export."""\n'
    import_line = f"from .{char_id} import {class_name}\n"
    if import_line not in init_text:
        if not init_text.endswith("\n"):
            init_text += "\n"
        init_text += import_line
        try:
            ast.parse(init_text)
        except SyntaxError as e:
            return se._fail(f"characters/__init__.py 갱신 실패: {e}")
        se._safe_write(init_path, init_text)

    # 2. schema.py 수정 — import + chars 리스트 + (필요시) narrator/pc.
    schema_path = pkg / "schema.py"
    if not schema_path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = schema_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    world_call = _find_world_call(tree)
    if world_call is None:
        return se._fail("SCENARIOS 의 World(chars=[...]) 호출을 찾지 못했습니다.")
    chars_kw = next((k for k in world_call.keywords if k.arg == "chars"), None)
    if chars_kw is None or not isinstance(chars_kw.value, ast.List):
        return se._fail("chars=[...] 리스트를 찾지 못했습니다.")

    # 이미 등록돼 있으면(클래스 호출이 chars 에 있음) chars 삽입은 건너뛴다.
    already = any(isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == class_name
                  for e in chars_kw.value.elts)

    edits: list[tuple[int, int, str]] = []  # (start, end, new_src) — 우→좌 적용

    # 2a. import 라인 (마지막 최상위 import 뒤).
    import_stmt = f"from src.assets.worlds.{world_id}.characters import {class_name}\n"
    if import_stmt not in text:
        last_import = None
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                last_import = stmt
        pos = line_offsets[last_import.end_lineno + 1] if last_import and last_import.end_lineno + 1 < len(line_offsets) else 0
        edits.append((pos, pos, import_stmt))

    # 2b. chars 리스트에 ClassName() 추가 ('[' 직후 prepend).
    if not already:
        c_start, _c_end = se._node_span(text, chars_kw.value, line_offsets)
        edits.append((c_start + 1, c_start + 1, f"{class_name}(), "))

    # 2c. narrator/pc 가 None 이면 채운다(첫 캐릭터/첫 PC 기준).
    def _kw_none_span(arg_name: str):
        kw = next((k for k in world_call.keywords if k.arg == arg_name), None)
        if kw and isinstance(kw.value, ast.Constant) and kw.value.value is None:
            return se._node_span(text, kw.value, line_offsets)
        return None

    nar_span = _kw_none_span("narrator")
    if nar_span:
        edits.append((nar_span[0], nar_span[1], f"{class_name}()"))
    if char_type == "PC":
        pc_span = _kw_none_span("pc")
        if pc_span:
            edits.append((pc_span[0], pc_span[1], f"{class_name}()"))

    # 우→좌(시작 오프셋 내림차순)로 적용해 앞쪽 오프셋이 깨지지 않게 한다.
    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(schema_path, new_text)
    except OSError as e:
        return se._fail(f"schema.py 기록 실패: {e}")
    return se._ok(f"{class_name} 등록 완료.", backup)


def _find_world_call(tree: ast.Module) -> ast.Call | None:
    """SCENARIOS 의 World(chars=[...]) 생성 호출을 찾습니다.

    chars 의 '값이 리스트 리터럴'인 호출만 인정 — super().__init__(chars=chars or []) 처럼
    chars 가 리터럴이 아닌 호출(BoolOp/Name)은 제외해야 정확히 SCENARIOS 쪽을 잡는다.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for k in node.keywords:
                if k.arg == "chars" and isinstance(k.value, ast.List):
                    return node
    return None


# ──────────────────────────────────────────────────────────────────────
# 시나리오별 등장인물 관리 (SCENARIOS 의 각 World(chars=[...]) 편집)
# ──────────────────────────────────────────────────────────────────────


def _class_str_attr(cls: ast.ClassDef, attr: str) -> str | None:
    """클래스 body 의 `attr = "..."` 문자열 리터럴 값을 추출합니다."""
    for stmt in cls.body:
        names, value = se._assign_target_names(stmt)
        if attr in names and value is not None:
            try:
                v = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
            return v if isinstance(v, str) else None
    return None


def list_all_characters(world_id: str) -> list[dict]:
    """월드에 정의된 모든 캐릭터를 반환합니다 (시나리오 무관). [{char_id, class_name, name}]."""
    pkg = world_pkg_dir(world_id)
    candidates: list[Path] = []
    char_dir = pkg / "characters"
    if char_dir.is_dir():
        candidates.extend(char_dir.rglob("*.py"))
    single = pkg / "characters.py"
    if single.is_file():
        candidates.append(single)
    schema = pkg / "schema.py"
    if schema.is_file():
        candidates.append(schema)

    out: list[dict] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for cls in se._iter_classes(tree):
            cid = se._class_id_value(cls)
            if not cid or cid in seen:  # id 가 빈 문자열인 베이스 클래스 등은 제외
                continue
            seen.add(cid)
            out.append({"char_id": cid, "class_name": cls.name, "name": _class_str_attr(cls, "name") or cid})
    out.sort(key=lambda c: c["char_id"])
    return out


def _find_scenario_world_call(tree: ast.Module, scenario_id: str) -> ast.Call | None:
    """scenario_id 와 일치하는 Scenario(...) 의 world=World(...) 호출을 찾습니다.

    Scenario 호출은 scenario_id 와 world 키워드를 모두 가진다 — 이 둘로 식별한다.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        sid_kw = next((k for k in node.keywords if k.arg == "scenario_id"), None)
        world_kw = next((k for k in node.keywords if k.arg == "world"), None)
        if (sid_kw and world_kw and isinstance(sid_kw.value, ast.Constant)
                and sid_kw.value.value == scenario_id and isinstance(world_kw.value, ast.Call)):
            return world_kw.value
    return None


def _find_scenario_call(tree: ast.Module, scenario_id: str) -> ast.Call | None:
    """scenario_id 와 일치하는 Scenario(...) 호출을 찾습니다."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Scenario"):
            continue
        sid_kw = next((k for k in node.keywords if k.arg == "scenario_id"), None)
        if sid_kw and isinstance(sid_kw.value, ast.Constant) and sid_kw.value.value == scenario_id:
            return node
    return None


def _replace_or_insert_call_keyword(text: str, call: ast.Call, key: str, value: object) -> str:
    """Call 키워드 값을 치환하거나, 없으면 호출 끝에 새 키워드를 삽입한 소스를 반환합니다."""
    line_offsets = se._line_offsets(text)
    value_kw = next((kw for kw in call.keywords if kw.arg == key), None)
    if value_kw is not None:
        start, end = se._node_span(text, value_kw.value, line_offsets)
        base_indent = se._base_indent(text, value_kw.value, line_offsets)
        return se._replace_node_span(text, start, end, se._emit(value, base_indent))

    call_start, call_end = se._node_span(text, call, line_offsets)
    call_src = text[call_start:call_end]
    base_indent = se._base_indent(text, call, line_offsets)
    inner_indent = base_indent + "    "
    if "\n" in call_src:
        value_src = se._emit(value, inner_indent)
        insertion = f"{inner_indent}{key}={value_src},\n{base_indent}"
    else:
        value_src = se._emit(value, base_indent)
        insertion = f", {key}={value_src}"
    return se._replace_node_span(text, call_end - 1, call_end - 1, insertion)


def _scenario_chars_kw(tree: ast.Module, scenario_id: str | None) -> ast.keyword | None:
    """해당 시나리오 World 호출의 chars 키워드(리스트 값)를 반환합니다. 없으면 첫 World 호출로 폴백."""
    wc = _find_scenario_world_call(tree, scenario_id) if scenario_id else None
    if wc is None:
        wc = _find_world_call(tree)
    if wc is None:
        return None
    kw = next((k for k in wc.keywords if k.arg == "chars"), None)
    return kw if (kw and isinstance(kw.value, ast.List)) else None


def get_scenario_characters(world_id: str, scenario_id: str | None) -> list[str]:
    """해당 시나리오의 chars=[ClassName(), ...] 에서 char_id 목록을 추출합니다.

    schema.py 의 chars 리스트에 없는 캐릭터도 월드에 파일이 존재하면 목록 끝에 추가합니다.
    (새 캐릭터 추가 후 schema.py 를 수동 편집하지 않아도 오른쪽 패널과 중앙 패널이 동기화됩니다.)
    """
    all_chars = list_all_characters(world_id)
    all_cids = [c["char_id"] for c in all_chars]

    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return all_cids
    tree = ast.parse(path.read_text(encoding="utf-8"))
    kw = _scenario_chars_kw(tree, scenario_id)
    if kw is None:
        return all_cids
    cn_to_cid = {c["class_name"]: c["char_id"] for c in all_chars}
    out: list[str] = []
    for elt in kw.value.elts:  # type: ignore[attr-defined]
        if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name):
            cid = cn_to_cid.get(elt.func.id)
            if cid:
                out.append(cid)
    # schema.py 의 chars 에 없는 캐릭터를 순서 유지하며 뒤에 추가
    listed = set(out)
    for cid in all_cids:
        if cid not in listed:
            out.append(cid)
    return out


def _emit_chars_list(class_names: list[str], base_indent: str) -> str:
    """[ClassName(), ...] 소스를 생성합니다 (비면 [])."""
    if not class_names:
        return "[]"
    inner = base_indent + "    "
    lines = ["["]
    for cn in class_names:
        lines.append(f"{inner}{cn}(),")
    lines.append(base_indent + "]")
    return "\n".join(lines)


def set_scenario_characters(world_id: str, scenario_id: str | None, char_ids: list[str]) -> dict:
    """해당 시나리오의 chars 리스트를 char_ids 로 교체합니다(필요한 import 도 함께 보강)."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    kw = _scenario_chars_kw(tree, scenario_id)
    if kw is None:
        return se._fail("시나리오의 chars=[...] 리스트를 찾지 못했습니다.")

    cid_to_cn = {c["char_id"]: c["class_name"] for c in list_all_characters(world_id)}
    class_names: list[str] = []
    for cid in char_ids:
        cn = cid_to_cn.get(cid)
        if cn is None:
            return se._fail(f"알 수 없는 캐릭터: {cid}")
        class_names.append(cn)

    edits: list[tuple[int, int, str]] = []

    # 필요한 import 보강 (chars 에 쓰는 클래스가 schema 에 import 돼 있어야 컴파일됨).
    needed = [f"from src.assets.worlds.{world_id}.characters import {cn}\n"
              for cn in class_names if f"import {cn}\n" not in text and f"import {cn}" not in text]
    if needed:
        last_import = None
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                last_import = stmt
        pos = line_offsets[last_import.end_lineno + 1] if last_import and last_import.end_lineno + 1 < len(line_offsets) else 0
        edits.append((pos, pos, "".join(needed)))

    # chars 리스트 노드 전체 교체.
    c_start, c_end = se._node_span(text, kw.value, line_offsets)
    base_indent = se._base_indent(text, kw.value, line_offsets)
    edits.append((c_start, c_end, _emit_chars_list(class_names, base_indent)))

    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id or 'default'}' 등장인물을 갱신했습니다 ({len(class_names)}명).", backup)


def _find_world_class_name(tree: ast.Module) -> str | None:
    """SCENARIOS 의 World(chars=[...]) 호출에서 World 서브클래스명을 추출합니다."""
    wc = _find_world_call(tree)
    if wc is not None and isinstance(wc.func, ast.Name):
        return wc.func.id
    return None


def _migrate_legacy_scenarios(text: str) -> str | None:
    """레거시 world_instance 스타일 schema.py 에 SCENARIOS 리스트 뼈대를 삽입합니다.

    world_instance 대입 뒤에 SCENARIOS: list[Scenario] = [Scenario(world=world_instance)] 를 추가.
    Scenario import 가 없으면 함께 추가. 실패하면 None 반환.
    """
    tree = ast.parse(text)

    # world_instance 대입문 찾기
    world_inst_stmt: ast.stmt | None = None
    for stmt in tree.body:
        names, _ = se._assign_target_names(stmt)
        if "world_instance" in names:
            world_inst_stmt = stmt
            break
    if world_inst_stmt is None:
        return None

    line_offsets = se._line_offsets(text)
    _start, end = se._node_span(text, world_inst_stmt, line_offsets)

    scenarios_block = (
        "\n\nSCENARIOS: list[Scenario] = [\n"
        "    Scenario(\n"
        '        scenario_id="default",\n'
        '        display_name="기본",\n'
        "        world=world_instance,\n"
        "    ),\n"
        "]\n"
    )
    new_text = text[:end] + scenarios_block + text[end:]

    # Scenario import 보장
    new_text = _ensure_base_import(new_text, "Scenario")

    try:
        ast.parse(new_text)
    except SyntaxError:
        return None
    return new_text


def create_scenario(world_id: str, scenario_id: str, display_name: str) -> dict:
    """SCENARIOS 리스트에 빈 chars 의 새 Scenario(...) 항목을 추가합니다.

    SCENARIOS 리스트가 없는 레거시 세계관(world_instance 스타일)은 자동으로 SCENARIOS 뼈대를 삽입합니다.
    """
    if not _SID_RE.match(scenario_id or ""):
        return se._fail("scenario_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    # SCENARIOS = [...] 리스트 노드 찾기.
    scenarios_list: ast.List | None = None
    for stmt in tree.body:
        names, value = se._assign_target_names(stmt)
        if "SCENARIOS" in names and isinstance(value, ast.List):
            scenarios_list = value
            break

    # 레거시 world_instance 스타일이면 SCENARIOS 뼈대를 자동 삽입한다.
    if scenarios_list is None:
        migrated = _migrate_legacy_scenarios(text)
        if migrated is None:
            return se._fail(
                "SCENARIOS 리스트를 찾지 못했습니다. "
                "world_editor 로 생성된 세계관이거나 world_instance 가 있어야 합니다."
            )
        text = migrated
        tree = ast.parse(text)
        line_offsets = se._line_offsets(text)
        for stmt in tree.body:
            names, value = se._assign_target_names(stmt)
            if "SCENARIOS" in names and isinstance(value, ast.List):
                scenarios_list = value
                break
        if scenarios_list is None:
            return se._fail("SCENARIOS 마이그레이션 후 리스트를 찾지 못했습니다.")

    # 중복 scenario_id 방지 (_find_scenario_call 로 world=world_instance 스타일도 검출).
    if _find_scenario_call(tree, scenario_id) is not None:
        return se._fail(f"이미 존재하는 시나리오입니다: {scenario_id}")

    world_cls = _find_world_class_name(tree)
    if not world_cls:
        return se._fail("World 클래스명을 찾지 못했습니다.")

    display = (display_name or scenario_id).replace('"', "'")
    base_indent = se._base_indent(text, scenarios_list, line_offsets)
    inner = base_indent + "    "
    entry = (
        f"{inner}Scenario(\n"
        f'{inner}    scenario_id="{scenario_id}",\n'
        f'{inner}    display_name="{display}",\n'
        f"{inner}    world={world_cls}(\n"
        f"{inner}        narrator=None,\n"
        f"{inner}        pc=None,\n"
        f"{inner}        chars=[],\n"
        f'{inner}        scenario_id="{scenario_id}",\n'
        f"{inner}    ),\n"
        f"{inner}),\n"
    )
    # 리스트 닫는 ']' 직전에 삽입.
    # 기존 마지막 항목에 trailing comma 가 없으면 먼저 추가한다.
    _start, end = se._node_span(text, scenarios_list, line_offsets)
    insert_pos = end - 1
    prefix = text[:insert_pos].rstrip()
    if scenarios_list.elts and not prefix.endswith(","):
        # trailing comma 추가: rstrip 위치 바로 뒤에 ',' 삽입
        comma_pos = len(prefix)
        text = text[:comma_pos] + "," + text[comma_pos:]
        # 오프셋이 바뀌었으므로 재계산
        line_offsets = se._line_offsets(text)
        tree = ast.parse(text)
        for stmt in tree.body:
            names, value = se._assign_target_names(stmt)
            if "SCENARIOS" in names and isinstance(value, ast.List):
                scenarios_list = value
                break
        _start, end = se._node_span(text, scenarios_list, line_offsets)
        insert_pos = end - 1
    new_text = text[:insert_pos] + entry + text[insert_pos:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"시나리오 추가 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id}' 를 추가했습니다.", backup)


def update_scenario_meta(world_id: str, scenario_id: str, display_name: str) -> dict:
    """SCENARIOS 안의 Scenario(...).display_name 값을 갱신합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    call = _find_scenario_call(tree, scenario_id)
    if call is None:
        return se._fail(f"시나리오를 찾지 못했습니다: {scenario_id}")
    display_kw = next((k for k in call.keywords if k.arg == "display_name"), None)
    if display_kw is None or not isinstance(display_kw.value, ast.Constant):
        return se._fail("display_name 리터럴을 찾지 못했습니다. 소스에서 직접 편집하세요.")

    new_display = display_name.strip() or scenario_id
    start, end = se._node_span(text, display_kw.value, line_offsets)
    new_text = se._replace_node_span(text, start, end, repr(new_display))
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id}' 표시 이름을 갱신했습니다.", backup)


def _scenario_id_constant_edits(call: ast.Call, text: str, line_offsets: list[int], old_sid: str, new_sid: str) -> list[tuple[int, int, str]]:
    """Scenario(...) 하위 호출 안에서 old_sid 문자열 상수 교체 edit 목록을 만듭니다."""
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and node.value == old_sid:
            start, end = se._node_span(text, node, line_offsets)
            edits.append((start, end, repr(new_sid)))
    return edits


def _character_source_files(world_id: str) -> list[Path]:
    """월드 패키지에서 캐릭터 class가 있을 수 있는 Python 파일 목록을 반환합니다."""
    pkg = world_pkg_dir(world_id)
    candidates: list[Path] = []
    char_dir = pkg / "characters"
    if char_dir.is_dir():
        candidates.extend(char_dir.rglob("*.py"))
    single = pkg / "characters.py"
    if single.is_file():
        candidates.append(single)
    schema = pkg / "schema.py"
    if schema.is_file():
        candidates.append(schema)
    return candidates


def _rename_override_keys(path: Path, old_sid: str, new_sid: str) -> str | None:
    """파일 내 SCENARIO_OVERRIDES clean literal dict의 old_sid 키를 new_sid로 바꾼 새 텍스트를 반환합니다."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    edits: list[tuple[int, int, str]] = []
    for cls in se._iter_classes(tree):
        node = se._class_attr_node(cls, "SCENARIO_OVERRIDES")
        if node is None:
            continue
        if not isinstance(node, ast.Dict) or not se._is_clean_literal_node(node):
            continue
        overrides = ast.literal_eval(node)
        if not isinstance(overrides, dict) or old_sid not in overrides:
            continue
        if new_sid in overrides:
            raise ValueError(f"{path.name}: SCENARIO_OVERRIDES 에 이미 {new_sid!r} 키가 있습니다.")
        renamed = dict(overrides)
        renamed[new_sid] = renamed.pop(old_sid)
        start, end = se._node_span(text, node, line_offsets)
        base_indent = se._base_indent(text, node, line_offsets)
        edits.append((start, end, se._emit(renamed, base_indent)))
    if not edits:
        return None
    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]
    ast.parse(new_text)
    return new_text


def rename_scenario(world_id: str, old_scenario_id: str, new_scenario_id: str) -> dict:
    """시나리오 id를 schema.py, prompt/scenarios 폴더, 캐릭터 override key에서 함께 변경합니다."""
    if not _SID_RE.match(new_scenario_id or ""):
        return se._fail("new_scenario_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    if old_scenario_id == new_scenario_id:
        return se._fail("새 scenario_id 가 기존 값과 같습니다.")

    pkg = world_pkg_dir(world_id)
    schema_path = pkg / "schema.py"
    if not schema_path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")

    prompt_root = pkg / "prompt" / "scenarios"
    old_prompt_dir = prompt_root / old_scenario_id
    new_prompt_dir = prompt_root / new_scenario_id
    if new_prompt_dir.exists():
        return se._fail(f"대상 prompt/scenarios 폴더가 이미 있습니다: {new_scenario_id}")

    schema_text = schema_path.read_text(encoding="utf-8")
    schema_tree = ast.parse(schema_text)
    if _find_scenario_call(schema_tree, new_scenario_id) is not None:
        return se._fail(f"이미 존재하는 시나리오입니다: {new_scenario_id}")
    call = _find_scenario_call(schema_tree, old_scenario_id)
    if call is None:
        return se._fail(f"시나리오를 찾지 못했습니다: {old_scenario_id}")

    line_offsets = se._line_offsets(schema_text)
    edits = _scenario_id_constant_edits(call, schema_text, line_offsets, old_scenario_id, new_scenario_id)
    if not edits:
        return se._fail("변경할 scenario_id 리터럴을 찾지 못했습니다.")
    new_schema_text = schema_text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_schema_text = new_schema_text[:start] + src + new_schema_text[end:]
    try:
        ast.parse(new_schema_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")

    char_texts: list[tuple[Path, str]] = []
    try:
        for path in _character_source_files(world_id):
            if path == schema_path:
                continue
            renamed = _rename_override_keys(path, old_scenario_id, new_scenario_id)
            if renamed is not None:
                char_texts.append((path, renamed))
    except (OSError, SyntaxError, ValueError) as e:
        return se._fail(f"캐릭터 override key 갱신 준비 실패: {e}")

    backups: list[str] = []
    try:
        backups.append(se._safe_write(schema_path, new_schema_text))
        for path, new_text in char_texts:
            backups.append(se._safe_write(path, new_text))
        if old_prompt_dir.exists():
            old_prompt_dir.rename(new_prompt_dir)
    except OSError as e:
        return se._fail(f"scenario_id 변경 중 파일 기록 실패: {e}")

    return {
        "ok": True,
        "message": f"시나리오 id를 '{old_scenario_id}'에서 '{new_scenario_id}'로 변경했습니다.",
        "backup": "; ".join(backups),
        "formatted": True,
    }


def update_scene_types(world_id: str, scene_types: dict[str, str], scenario_id: str | None = None) -> dict:
    """World 클래스 또는 Scenario.scene_types dict 리터럴을 치환합니다."""
    if not isinstance(scene_types, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in scene_types.items()):
        return se._fail("scene_types 는 str→str dict 여야 합니다.")

    source = _load_schema_source(world_id)
    if isinstance(source, dict):
        return source

    if scenario_id:
        new_text = _replace_scenario_keyword(source, scenario_id, "scene_types", scene_types)
        if isinstance(new_text, dict):
            return new_text
        return _write_schema_source(source, new_text, f"시나리오 '{scenario_id}' scene_types 를 갱신했습니다.")

    cls = _find_world_class(source.tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")
    new_text = _replace_class_attr_literal(source, cls, "SCENE_TYPES", scene_types)
    if isinstance(new_text, dict):
        return new_text
    new_text = _remove_init_scene_types_override(new_text)
    if isinstance(new_text, dict):
        return new_text
    return _write_schema_source(source, new_text, "SCENE_TYPES 를 갱신했습니다.")


def update_default_perspective(world_id: str, perspective: object, scenario_id: str | None = None) -> dict:
    """World 클래스 또는 시나리오 World(...)의 perspective 리터럴을 치환합니다."""
    if not (
        isinstance(perspective, int)
        or (
            isinstance(perspective, list)
            and len(perspective) in (2, 3)
            and isinstance(perspective[0], int)
            and isinstance(perspective[1], str)
            and (len(perspective) == 2 or isinstance(perspective[2], bool))
        )
    ):
        return se._fail("perspective 는 정수, [정수, 문자열], 또는 [정수, 문자열, 불리언] 이어야 합니다.")
    value: object = tuple(perspective) if isinstance(perspective, list) else perspective

    source = _load_schema_source(world_id)
    if isinstance(source, dict):
        return source

    if scenario_id:
        new_text = _replace_scenario_world_keyword(source, scenario_id, "perspective", value)
        if isinstance(new_text, dict):
            return new_text
        return _write_schema_source(source, new_text, f"시나리오 '{scenario_id}' perspective 를 갱신했습니다.")

    cls = _find_world_class(source.tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")
    new_text = _replace_class_attr_literal(source, cls, "DEFAULT_PERSPECTIVE", value)
    if isinstance(new_text, dict):
        return new_text
    return _write_schema_source(source, new_text, "DEFAULT_PERSPECTIVE 를 갱신했습니다.")


# 예약된 Kuzu 노드 테이블명 — 커스텀 슬롯 label 과 충돌하면 안 된다.
_RESERVED_LABELS: frozenset[str] = frozenset({
    "Character", "StaticProfile", "DynamicInformation", "Personality", "DynamicState",
    "IntimateProfile", "WorkplaceProfile", "DialogueExamples", "Item", "Goal", "Secret",
    "Schedule", "Event", "Memory", "NeedsState", "Rule", "Location", "GlobalState",
    "SpeechProfile", "RelationshipProfile", "StaticEvent", "PersonalFact",
    "KakaoRoom", "KakaoMessage",
})


def _find_extra_slots_node(cls: ast.ClassDef) -> ast.List | None:
    """World 클래스 body 에서 EXTRA_SLOTS = [...] 리스트 리터럴을 찾습니다."""
    node = _find_class_attr(cls, "EXTRA_SLOTS")
    return node if isinstance(node, ast.List) else None


def _insert_extra_slots_attr(path: Path, cls: ast.ClassDef, slots: list) -> dict:
    """World 클래스 body 에 EXTRA_SLOTS = [...] 클래스 속성을 삽입합니다.

    SCENE_TYPES 바로 뒤에 삽입하고, 없으면 마지막 class-level 할당 뒤에 삽입합니다.
    """
    text = path.read_text(encoding="utf-8")
    line_offsets = se._line_offsets(text)
    body_indent = " " * cls.body[0].col_offset

    # 삽입 기준 노드: SCENE_TYPES > 마지막 Assign/AnnAssign > 첫 body 문장.
    target = None
    for stmt in cls.body:
        names, _ = se._assign_target_names(stmt)
        if "SCENE_TYPES" in names:
            target = stmt
            break
    if target is None:
        for stmt in cls.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                target = stmt
    if target is None:
        target = cls.body[0]

    _, end = se._node_span(text, target, line_offsets)
    val_src = se._emit(slots, body_indent)
    snippet = f"\n{body_indent}EXTRA_SLOTS: list = {val_src}"
    new_text = text[:end] + snippet + text[end:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"EXTRA_SLOTS 삽입 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok("EXTRA_SLOTS 를 생성했습니다.", backup)


def add_extra_slot(world_id: str, slot_id: str, label: str, sub: str) -> dict:
    """World 클래스의 EXTRA_SLOTS 에 커스텀 캐릭터 슬롯을 추가합니다.

    slot_id: role 키 (예: "magic"). label: Kuzu 노드 테이블명 (예: "Magic"). sub: UI 설명.
    EXTRA_SLOTS 가 없으면 새로 생성하고, 이미 있으면 항목을 추가합니다.
    """
    if not slot_id or not slot_id.isidentifier():
        return se._fail("slot_id 는 유효한 식별자여야 합니다 (예: magic, ability).")
    if not label or not label.isidentifier():
        return se._fail("label 은 유효한 식별자여야 합니다 (Kuzu 노드 테이블명, 예: Magic).")
    if label in _RESERVED_LABELS:
        return se._fail(f"label '{label}' 은 기존 Kuzu 노드 테이블명과 충돌합니다.")
    if slot_id in {"static", "personality", "info", "state"}:
        return se._fail(f"slot_id '{slot_id}' 은 표준 슬롯과 충돌합니다.")

    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = _find_world_class(tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")

    new_slot: dict = {"id": slot_id, "label": label, "sub": sub or ""}
    node = _find_extra_slots_node(cls)

    if node is None:
        # EXTRA_SLOTS 가 없음 → 새로 삽입
        result = _insert_extra_slots_attr(path, cls, [new_slot])
        return result

    if not se._is_clean_literal_node(node):
        return se._fail("EXTRA_SLOTS 이 clean 리터럴이 아닙니다. 소스에서 직접 편집하세요.")

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        return _find_extra_slots_node(c) if c else None

    def _transform(old: list) -> list:
        if any(isinstance(s, dict) and s.get("id") == slot_id for s in old):
            return old  # 중복 방지 — 이미 있으면 그대로
        return list(old) + [new_slot]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate, f"슬롯 '{slot_id}' 을 추가했습니다.")


def delete_extra_slot(world_id: str, slot_id: str) -> dict:
    """World 클래스의 EXTRA_SLOTS 에서 커스텀 슬롯을 제거합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = _find_world_class(tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")

    node = _find_extra_slots_node(cls)
    if node is None:
        return se._fail("EXTRA_SLOTS 가 없습니다.")
    if not se._is_clean_literal_node(node):
        return se._fail("EXTRA_SLOTS 이 clean 리터럴이 아닙니다. 소스에서 직접 편집하세요.")

    current = ast.literal_eval(node)
    if not any(isinstance(s, dict) and s.get("id") == slot_id for s in current):
        return se._fail(f"슬롯 '{slot_id}' 이 없습니다.")

    def _locate(t: ast.Module):
        c = _find_world_class(t)
        return _find_extra_slots_node(c) if c else None

    def _transform(old: list) -> list:
        return [s for s in old if not (isinstance(s, dict) and s.get("id") == slot_id)]

    def _relocate(t: ast.Module):
        n = _locate(t)
        return ast.literal_eval(n) if n is not None else se._RELOCATE_MISS

    return _rewrite_literal(path, _locate, _transform, _relocate, f"슬롯 '{slot_id}' 을 삭제했습니다.")
