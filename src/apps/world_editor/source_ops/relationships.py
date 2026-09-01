# ================================
# src/apps/world_editor/source_ops/relationships.py
#
# Relationship source creation and deletion operations.
#
# Functions
#   - add_relationship(world_id: str, source: str, target: str, rel_type: str, affinity: int, trust: int, current_status: str) -> dict : Add a relationship.
#   - delete_relationship(world_id: str, source: str, target: str) -> dict : Delete a relationship.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

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

