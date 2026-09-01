# ================================
# src/apps/world_editor/source_ops/creator_support.py
#
# Shared schema-source and literal rewrite support for creation operations.
# ================================

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from src.apps.world_editor.source_ops import core as se
from src.apps.world_editor.worlds import world_pkg_dir

_find_class_attr = se._class_attr_node

_SID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


_EVENT_DEFAULTS: dict = {
    "summary": "", "timestamp": "2024-01-01T09:00:00", "importance": 5,
    "impact": "", "memory_type": "episodic", "decay_rate": 0.15,
    "narrative_summary": "", "state_summary": "", "summary_level": 0,
}


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


__all__ = [name for name in globals() if not name.startswith("__")]
