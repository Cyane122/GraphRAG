# ================================
# src/apps/world_editor/source_ops/rewrite.py
#
# Verified AST source replacement and relocation helpers.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.locators import *

_RELOCATE_MISS = object()  # relocate 콜백이 '대상을 다시 못 찾음'을 알리는 센티넬


def _apply_edit(
    path: Path,
    text: str,
    new_src: str,
    start: int,
    end: int,
    expected: object,
    relocate,
    message: str,
) -> dict:
    """치환 → 구문검증 → 재로케이트/literal_eval → 의미검증 → atomic write 를 수행합니다.

    스펙 5~9단계의 공통 구현. 어느 검증이든 실패하면 파일을 건드리지 않고
    실패 dict 를 반환합니다. relocate(new_text) 는 재파싱한 트리에서 방금 고친
    대상을 다시 찾아 'literal_eval 된 실제 값'을 돌려주는 콜백입니다(못 찾으면 _RELOCATE_MISS).
    값을 직접 돌려주므로 blob(키워드→dict 합성) 같은 케이스도 노드 span 계산 없이 검증됩니다.
    """
    # 5. 텍스트 치환.
    new_text = _replace_node_span(text, start, end, new_src)

    # 6. 구문 안전성 — 새 소스가 파싱되는가.
    try:
        new_tree = ast.parse(new_text)
    except SyntaxError as e:
        return _fail(f"치환 결과가 파싱되지 않습니다: {e}")

    # 7. 의미 안전성 — 재로케이트한 대상의 실제 리터럴 값이 의도값과 일치하는가.
    try:
        actual = relocate(new_tree)
    except (ValueError, SyntaxError) as e:
        return _fail(f"치환 후 의미 검증 실패: {e}")
    if actual is _RELOCATE_MISS:
        return _fail("치환 후 대상 노드를 다시 찾지 못했습니다.")

    # tuple/list 비교: emit 은 튜플을 튜플로 유지하므로 형까지 같아야 한다.
    if not _semantic_equal(actual, expected):
        return _fail("치환 후 값이 의도와 일치하지 않습니다.")

    # 8~9. 백업 + atomic write.
    try:
        backup = _safe_write(path, new_text)
    except OSError as e:
        return _fail(f"파일 기록 실패: {e}")
    return _ok(message, backup)


def _semantic_equal(actual: object, expected: object) -> bool:
    """치환 결과 리터럴이 의도값과 같은지 비교합니다.

    blob/state 의 경우 expected 는 dict, actual 도 dict 여야 하고 동치여야 합니다.
    관계/튜플-행은 tuple 끼리 비교합니다. list/tuple 혼동을 막기 위해 형도 따집니다.
    """
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual == expected
    if isinstance(expected, tuple):
        return isinstance(actual, tuple) and actual == expected
    return actual == expected


def _relocate_rel(tree: ast.Module, source: str, target: str) -> object:
    """치환된 트리에서 (source→target) 관계 값 노드를 다시 찾아 literal_eval 한 값을 반환합니다."""
    cls = _find_character_class(tree, source)
    method = _find_method(cls, "build_relationship") if cls else None
    if method is None:
        return _RELOCATE_MISS
    dicts = _find_rel_dicts(method)
    node, _ = _rel_value_node_for(dicts, target)
    if node is None:
        return _RELOCATE_MISS
    # literal_eval 은 4-튜플 리터럴을 tuple 로 돌려준다 — 의도값(tuple)과 형까지 일치.
    return ast.literal_eval(node)


def _relocate_class_dict(tree: ast.Module, char_id: str, attr: str) -> object:
    """치환된 트리에서 캐릭터 class attribute dict 를 다시 찾아 literal_eval 합니다."""
    cls = _find_character_class(tree, char_id)
    if cls is None:
        return _RELOCATE_MISS
    node = _class_attr_node(cls, attr)
    if node is None or not isinstance(node, ast.Dict):
        return _RELOCATE_MISS
    return ast.literal_eval(node)


def _relocate_blob(tree: ast.Module, char_id: str, label: str) -> object:
    """치환된 blob 호출의 키워드들을 {arg: literal_eval(value)} dict 로 재구성해 반환합니다.

    Call 노드는 literal_eval 대상이 아니므로, 노드 span 계산 없이 각 키워드 값만
    개별적으로 literal_eval 해 dict 를 만든다. 비리터럴 키워드가 섞이면 예외가 전파돼
    _apply_edit 가 안전하게 중단한다.
    """
    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _RELOCATE_MISS
    call, _ = _find_blob_call(method, label)
    if call is None:
        return _RELOCATE_MISS
    return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}


def _relocate_state(tree: ast.Module, char_id: str, scenario_id: str | None = None) -> object:
    """치환된 트리에서 무조건 _state dict 노드를 다시 찾아 literal_eval 한 값을 반환합니다."""
    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _RELOCATE_MISS
    node, _ = _find_state_dict(method, scenario_id)
    if node is None:
        return _RELOCATE_MISS
    return ast.literal_eval(node)


def _relocate_tuple_row(tree: ast.Module, kind: str, row_id: str) -> object:
    """치환된 트리에서 튜플-행 노드를 다시 찾아 literal_eval 한 값(tuple)을 반환합니다."""
    node, _ = _find_tuple_row(tree, kind, row_id)
    if node is None:
        return _RELOCATE_MISS
    return ast.literal_eval(node)


def _relocate_schedule(tree: ast.Module, char_id: str, schedule_id: str, keys: set[str]) -> object:
    """치환된 트리에서 schedule 호출을 다시 찾아 요청 키들의 리터럴 값을 반환합니다."""
    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _RELOCATE_MISS
    call, _ = _find_schedule_call(method, char_id, schedule_id)
    if call is None:
        return _RELOCATE_MISS
    kw_map = _call_kw_map(call)
    out: dict[str, object] = {}
    for key in keys:
        if key not in kw_map:
            return _RELOCATE_MISS
        out[key] = ast.literal_eval(kw_map[key].value)
    return out


__all__ = [name for name in globals() if not name.startswith("__")]
