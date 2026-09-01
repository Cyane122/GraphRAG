# ================================
# src/apps/world_editor/source_ops/slots.py
#
# Extra-slot source creation and deletion operations.
#
# Functions
#   - add_extra_slot(world_id: str, slot_id: str, label: str, sub: str) -> dict : Add an extra slot.
#   - delete_extra_slot(world_id: str, slot_id: str) -> dict : Delete an extra slot.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

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

