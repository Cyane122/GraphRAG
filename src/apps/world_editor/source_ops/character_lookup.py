# ================================
# src/apps/world_editor/source_ops/character_lookup.py
#
# Character-source file discovery and tuple-row literal evaluation helpers.
#
# Functions
#   - find_character_file(world_id: str, char_id: str) -> Path | None : Locate a character source file.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.locators import *

def find_character_file(world_id: str, char_id: str) -> Path | None:
    """char_id 클래스를 정의한 캐릭터 소스 파일 경로를 반환합니다 (없으면 None).

    탐색 범위: <world_pkg>/characters/**.py, <world_pkg>/characters.py, <world_pkg>/schema.py.
    각 파일을 ast 로 파싱해 body 에 `id = "<char_id>"` 할당을 가진 클래스가 있으면 그 파일.
    """
    editor = sys.modules.get("src.apps.world_editor.source_ops.editor")
    package_dir = getattr(editor, "world_pkg_dir", world_pkg_dir)
    pkg = package_dir(world_id)

    # 후보 파일 목록: characters/ 디렉터리 재귀 + 단일 characters.py.
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

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            # 읽기/파싱 실패 파일은 조용히 건너뛴다 — 탐색은 best-effort.
            continue
        if _find_character_class(tree, char_id) is not None:
            return path
    return None

def _eval_tuple_columns(node: ast.AST, kind: str) -> dict | None:
    """clean 리터럴 튜플 노드를 컬럼명→값 dict 로 변환합니다(arity/리터럴 불일치 시 None)."""
    if not _is_clean_literal_node(node):
        return None
    try:
        vals = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    cols = _TUPLE_COLUMNS.get(kind, ())
    if not isinstance(vals, tuple) or len(vals) != len(cols):
        return None
    return dict(zip(cols, vals))

__all__ = [name for name in globals() if not name.startswith("__")]
