# ================================
# src/apps/world_editor/source_ops/core.py
#
# Shared AST locators, literal checks, rewrite result helpers, and operation constants.
#
# Functions
#   - safe_write(path: Path, new_text: str) -> str : Back up and atomically replace source text.
#   - ok(message: str, backup: str) -> dict : Build a successful rewrite result.
#   - fail(message: str) -> dict : Build a failed rewrite result.
#   - find_class_attr(cls: ast.ClassDef, attr: str) -> ast.expr | None : Locate one direct class attribute.
#   - find_character_file(world_id: str, char_id: str) -> Path | None : Locate a character source file.
#   - annotate_graph(world_id: str, graph: dict) -> None : Annotate a compiled graph with editability metadata.
#   - edit_character_cfg(world_id: str, char_id: str, scope: str, scenario_id: str | None, values: dict) -> dict : Edit character configuration.
#   - edit_relationship(world_id: str, source: str, target: str, rel_type: str | None, affinity: int | None, trust: int | None, current_status: str | None) -> dict : Edit a relationship.
#   - edit_blob(world_id: str, char_id: str, role: str, props: dict, _label: str | None = None) -> dict : Edit a profile blob.
#   - edit_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict : Edit character state.
#   - edit_tuple_row(world_id: str, kind: str, row_id: str, values: dict) -> dict : Edit a tuple row.
#   - edit_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Edit schedule fields.
#   - rewrite_schedule_call(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Rewrite a schedule call.
# ================================

from __future__ import annotations

import ast
from pathlib import Path

from src.apps.world_editor.source_ops import editor as _edit
from src.apps.world_editor.state_normalize import normalize_state_fields
from src.apps.world_editor.source_ops.text import (
    _base_indent,
    _emit,
    _line_offsets,
    _literal_eval_segment,
    _node_span,
    _replace_node_span,
)

ROLE_LABEL = _edit._ROLE_LABEL
TUPLE_COLUMNS = _edit._TUPLE_COLUMNS
SCHEDULE_EDITABLE_FIELDS = _edit._SCHEDULE_EDITABLE_FIELDS


def safe_write(path: Path, new_text: str) -> str:
    """Back up and atomically replace source text through the stable implementation."""
    return _edit._safe_write(path, new_text)


def ok(message: str, backup: str) -> dict:
    """Build a successful source rewrite result through the stable implementation."""
    return _edit._ok(message, backup)


def fail(message: str) -> dict:
    """Build a failed source rewrite result through the stable implementation."""
    return _edit._fail(message)


def find_class_attr(cls: ast.ClassDef, attr: str) -> ast.expr | None:
    """Locate one direct class attribute through the canonical AST locator."""
    return _edit._class_attr_node(cls, attr)


# Private aliases preserve AST rewrite call sites while centralizing their imports.
_safe_write = safe_write
_ok = ok
_fail = fail
_find_class_attr = find_class_attr
_class_attr_node = find_class_attr
_assign_target_names = _edit._assign_target_names
_iter_classes = _edit._iter_classes
_class_id_value = _edit._class_id_value
_find_method = _edit._find_method
_find_character_class = _edit._find_character_class
_class_attr_dict = _edit._class_attr_dict
_character_cfg_meta = _edit._character_cfg_meta
_is_clean_literal_node = _edit._is_clean_literal_node
_find_rel_dicts = _edit._find_rel_dicts
_rel_value_node_for = _edit._rel_value_node_for
_find_blob_call = _edit._find_blob_call
_is_scenario_ref = _edit._is_scenario_ref
_scenario_test_matches = _edit._scenario_test_matches
_direct_state_dict = _edit._direct_state_dict
_find_conditional_state_dict = _edit._find_conditional_state_dict
_find_state_dict = _edit._find_state_dict
_find_tuple_row = _edit._find_tuple_row
_is_self_id = _edit._is_self_id
_eval_schedule_id_expr = _edit._eval_schedule_id_expr
_call_kw_map = _edit._call_kw_map
_schedule_source_key = _edit._schedule_source_key
_find_schedule_call = _edit._find_schedule_call
_schedule_edit_meta = _edit._schedule_edit_meta
_coerce_weekday_set = _edit._coerce_weekday_set
_quote_string_like = _edit._quote_string_like
_emit_like_old = _edit._emit_like_old
_eval_tuple_columns = _edit._eval_tuple_columns
_RELOCATE_MISS = _edit._RELOCATE_MISS
_SCHEDULE_REWRITE_FIELDS = _edit._SCHEDULE_REWRITE_FIELDS
_apply_edit = _edit._apply_edit
find_character_file = _edit.find_character_file
annotate_graph = _edit.annotate_graph
merge_cfg_dict = _edit.merge_cfg_dict
edit_character_cfg = _edit.edit_character_cfg
edit_relationship = _edit.edit_relationship
edit_blob = _edit.edit_blob
edit_state = _edit.edit_state
edit_tuple_row = _edit.edit_tuple_row
edit_schedule = _edit.edit_schedule
rewrite_schedule_call = _edit.rewrite_schedule_call
_ROLE_LABEL = ROLE_LABEL
_TUPLE_COLUMNS = TUPLE_COLUMNS
_SCHEDULE_EDITABLE_FIELDS = SCHEDULE_EDITABLE_FIELDS
