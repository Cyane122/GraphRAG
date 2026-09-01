# ================================
# src/apps/world_editor/source_ops/text.py
#
# Pure UTF-8-safe text and AST-span primitives used by source rewrite operations.
#
# Functions
#   - line_offsets(text: str) -> list[int] : Return 1-based line start codepoint offsets.
#   - byte_col_to_codepoint(line: str, byte_col: int) -> int : Convert a UTF-8 byte column to a codepoint column.
#   - node_span(text: str, node: ast.AST, line_offsets: list[int]) -> tuple[int, int] : Resolve an AST node to a codepoint span.
#   - base_indent(text: str, node: ast.AST, line_offsets: list[int]) -> str : Return the node line's leading whitespace.
#   - replace_node_span(text: str, start: int, end: int, new_src: str) -> str : Replace one codepoint span.
#   - literal_eval_segment(text: str, start: int, end: int) -> object : Evaluate one literal source span.
#   - emit(value: object, base_indent: str) -> str : Render a supported Python literal.
# ================================

from __future__ import annotations

import ast


def line_offsets(text: str) -> list[int]:
    """Return codepoint offsets indexed by the AST's one-based line numbers."""
    offsets = [0, 0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def byte_col_to_codepoint(line: str, byte_col: int) -> int:
    """Convert an AST UTF-8 byte column to a Python string codepoint column."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def node_span(text: str, node: ast.AST, line_offsets: list[int]) -> tuple[int, int]:
    """Resolve an AST node's byte offsets into an absolute codepoint span."""
    start_line = line_offsets[node.lineno]
    start_text = text[start_line:line_offsets[node.lineno + 1]] if node.lineno + 1 < len(line_offsets) else text[start_line:]
    end_line = line_offsets[node.end_lineno]  # type: ignore[index]
    end_text = text[end_line:line_offsets[node.end_lineno + 1]] if node.end_lineno + 1 < len(line_offsets) else text[end_line:]  # type: ignore[index]
    return (
        start_line + byte_col_to_codepoint(start_text, node.col_offset),
        end_line + byte_col_to_codepoint(end_text, node.end_col_offset),  # type: ignore[arg-type]
    )


def base_indent(text: str, node: ast.AST, line_offsets: list[int]) -> str:
    """Return the exact leading whitespace of the AST node's starting line."""
    line_start = line_offsets[node.lineno]
    line = text[line_start:line_offsets[node.lineno + 1]] if node.lineno + 1 < len(line_offsets) else text[line_start:]
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def replace_node_span(text: str, start: int, end: int, new_src: str) -> str:
    """Return text with the half-open codepoint span replaced by new source."""
    return text[:start] + new_src + text[end:]


def literal_eval_segment(text: str, start: int, end: int) -> object:
    """Evaluate one source span as a Python literal."""
    return ast.literal_eval(text[start:end])


def emit(value: object, base_indent: str) -> str:
    """Render a supported Python literal while preserving nested indentation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    inner = base_indent + "    "
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("dict 키는 모두 문자열이어야 합니다.")
        if not value:
            return "{}"
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{inner}{key!r}: {emit(item, inner)},")
        lines.append(base_indent + "}")
        return "\n".join(lines)
    if isinstance(value, set):
        if not value:
            return "set()"
        return "{" + ", ".join(emit(item, inner) for item in sorted(value, key=repr)) + "}"
    if isinstance(value, (list, tuple)):
        opening, closing = ("[", "]") if isinstance(value, list) else ("(", ")")
        if not value:
            return opening + closing
        lines = [opening]
        for item in value:
            lines.append(f"{inner}{emit(item, inner)},")
        lines.append(base_indent + closing)
        return "\n".join(lines)
    raise ValueError(f"지원하지 않는 리터럴 타입: {type(value).__name__}")


# Legacy private aliases are deliberate: source_text remains a compatibility facade.
_line_offsets = line_offsets
_byte_col_to_codepoint = byte_col_to_codepoint
_node_span = node_span
_base_indent = base_indent
_replace_node_span = replace_node_span
_literal_eval_segment = literal_eval_segment
_emit = emit
