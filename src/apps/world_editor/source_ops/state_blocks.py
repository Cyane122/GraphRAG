# ================================
# src/apps/world_editor/source_ops/state_blocks.py
#
# Blob, state, subnode, alias, and schedule source creation operations.
#
# Functions
#   - set_blob(world_id: str, char_id: str, role: str, props: dict) -> dict : Upsert a profile blob.
#   - set_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict : Set character state.
#   - edit_subnode(world_id: str, char_id: str, node_id: str, fields: dict) -> dict : Edit a subnode.
#   - add_subnode(world_id: str, char_id: str, kind: str, fields: dict) -> dict : Add a subnode.
#   - add_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Add a schedule.
#   - set_aliases(world_id: str, char_id: str, aliases: list[str]) -> dict : Set aliases.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

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

