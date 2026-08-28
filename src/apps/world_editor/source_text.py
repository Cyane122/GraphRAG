# ================================
# src/apps/world_editor/source_text.py
#
# Compatibility facade for the world-editor's UTF-8-safe AST text primitives.
#
# Functions
#   - _line_offsets(text: str) -> list[int] : Return one-based source line offsets.
#   - _byte_col_to_codepoint(line: str, byte_col: int) -> int : Convert a UTF-8 byte column.
#   - _node_span(text: str, node: ast.AST, line_offsets: list[int]) -> tuple[int, int] : Resolve an AST source span.
#   - _base_indent(text: str, node: ast.AST, line_offsets: list[int]) -> str : Return leading source indentation.
#   - _replace_node_span(text: str, start: int, end: int, new_src: str) -> str : Replace a source span.
#   - _literal_eval_segment(text: str, start: int, end: int) -> object : Evaluate a literal source span.
#   - _emit(value: object, base_indent: str) -> str : Render a Python literal.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.text import (
    _base_indent,
    _byte_col_to_codepoint,
    _emit,
    _line_offsets,
    _literal_eval_segment,
    _node_span,
    _replace_node_span,
)
