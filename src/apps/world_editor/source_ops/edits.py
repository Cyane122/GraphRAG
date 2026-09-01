# ================================
# src/apps/world_editor/source_ops/edits.py
#
# Public AST edit operations for character, relationship, row, blob, state, and schedule source literals.
#
# Functions
#   - edit_character_cfg(world_id: str, char_id: str, scope: str, scenario_id: str | None, values: dict) -> dict : Edit character configuration.
#   - edit_relationship(world_id: str, source: str, target: str, rel_type: str | None, affinity: int | None, trust: int | None, current_status: str | None) -> dict : Edit a relationship.
#   - edit_blob(world_id: str, char_id: str, role: str, props: dict, _label: str | None = None) -> dict : Edit a profile blob.
#   - edit_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict : Edit character state.
#   - edit_tuple_row(world_id: str, kind: str, row_id: str, values: dict) -> dict : Edit a tuple row.
#   - edit_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Edit schedule fields.
#   - rewrite_schedule_call(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Rewrite a schedule call.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.locators import *
from src.apps.world_editor.source_ops.rewrite import *
from src.apps.world_editor.state_normalize import normalize_cfg_state_values, normalize_state_fields
from src.apps.world_editor.worlds import world_pkg_dir

_SCHEDULE_REWRITE_FIELDS: tuple[str, ...] = (
    "name", "activity", "summary", "prompt_hint", "prompt_priority", "material",
    "recurrence", "day_of_weeks", "date", "start_time", "end_time",
    "location_id", "status", "tags",
)

def _insert_class_dict_attr(path: Path, text: str, cls: ast.ClassDef, attr: str, value: dict, message: str) -> dict:
    """클래스 body 에 새 dict class attribute 를 삽입하고 파일을 저장합니다."""
    line_offsets = _line_offsets(text)
    first_method = next((stmt for stmt in cls.body if isinstance(stmt, ast.FunctionDef)), None)
    if first_method is None:
        return _fail("삽입 위치를 찾지 못했습니다.")
    insert_pos = line_offsets[first_method.lineno]
    indent = " " * first_method.col_offset
    attr_src = f"{indent}{attr} = {_emit(value, indent)}\n\n"
    new_text = text[:insert_pos] + attr_src + text[insert_pos:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return _fail(f"삽입 결과가 파싱되지 않습니다: {e}")
    try:
        backup = _safe_write(path, new_text)
    except OSError as e:
        return _fail(f"파일 기록 실패: {e}")
    return _ok(message, backup)


def _edit_class_dict_attr(path: Path, text: str, cls: ast.ClassDef, attr: str, value: dict, message: str) -> dict:
    """클래스 body 의 dict class attribute 를 치환하거나 없으면 생성합니다."""
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        return _fail("values 는 str 키를 가진 dict 여야 합니다.")
    node = _class_attr_node(cls, attr)
    if node is None:
        return _insert_class_dict_attr(path, text, cls, attr, value, message)
    if not isinstance(node, ast.Dict) or not _is_clean_literal_node(node):
        return _fail(f"편집 불가: {attr} 이 clean 리터럴 dict 가 아닙니다.")

    line_offsets = _line_offsets(text)
    start, end = _node_span(text, node, line_offsets)
    base_indent = _base_indent(text, node, line_offsets)
    return _apply_edit(
        path,
        text,
        _emit(value, base_indent),
        start,
        end,
        expected=value,
        relocate=lambda t: _relocate_class_dict(t, _class_id_value(cls) or "", attr),
        message=message,
    )


def edit_character_cfg(
    world_id: str,
    char_id: str,
    scope: str,
    scenario_id: str | None,
    values: dict,
) -> dict:
    """캐릭터 DEFAULT_CFG 또는 SCENARIO_OVERRIDES[scenario_id] 를 치환합니다.

    DEFAULT_CFG 는 기본값 전체, SCENARIO_OVERRIDES[scenario_id] 는 delta 만 저장합니다.
    두 class attribute 가 없으면 Character 베이스의 빈 dict 상속으로 보고 새 리터럴을 생성합니다.
    """
    if scope not in {"default", "override"}:
        return _fail("scope 는 'default' 또는 'override' 여야 합니다.")
    if not isinstance(values, dict) or not all(isinstance(k, str) for k in values):
        return _fail("values 는 str 키를 가진 dict 여야 합니다.")
    values = normalize_cfg_state_values(values)

    path = find_character_file(world_id, char_id)
    if path is None:
        return _fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = _find_character_class(tree, char_id)
    if cls is None:
        return _fail(f"캐릭터 클래스를 찾지 못했습니다: {char_id}")

    if scope == "default":
        return _edit_class_dict_attr(path, text, cls, "DEFAULT_CFG", values, f"{char_id} 의 DEFAULT_CFG 를 갱신했습니다.")

    sid = scenario_id or "default"
    overrides, editable, reason = _class_attr_dict(cls, "SCENARIO_OVERRIDES")
    if not editable:
        return _fail(f"편집 불가: {reason}")
    new_overrides = dict(overrides)
    if values:
        new_overrides[sid] = values
    else:
        new_overrides.pop(sid, None)
    return _edit_class_dict_attr(
        path,
        text,
        cls,
        "SCENARIO_OVERRIDES",
        new_overrides,
        f"{char_id} 의 SCENARIO_OVERRIDES[{sid}] 를 갱신했습니다.",
    )


def edit_relationship(
    world_id: str,
    source: str,
    target: str,
    rel_type: str | None,
    affinity: int | None,
    trust: int | None,
    current_status: str | None,
) -> dict:
    """(source→target) 관계 엣지의 4-튜플 값을 소스 파일에서 surgical 치환합니다.

    None 인자는 기존 튜플 값을 유지합니다. 튜플 순서는
    (rel_type, affinity, trust, current_status). 안전 절차(8단계)를 모두 통과해야 기록합니다.
    """
    path = find_character_file(world_id, source)
    if path is None:
        return _fail(f"source 캐릭터 파일을 찾지 못했습니다: {source}")

    # 1. 읽기 + 파싱.
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)

    # 2. build_relationship 내 target 키의 값 노드 로케이트.
    cls = _find_character_class(tree, source)
    method = _find_method(cls, "build_relationship") if cls else None
    if method is None:
        return _fail("build_relationship 메서드를 찾지 못했습니다.")
    dicts = _find_rel_dicts(method)
    node, reason = _rel_value_node_for(dicts, target)
    if node is None:
        return _fail(f"편집 불가: {reason}")

    start, end = _node_span(text, node, line_offsets)

    # 3. 현재 span 이 길이 4 리터럴인지 재확인 (literal 타게팅 증명).
    try:
        old_value = _literal_eval_segment(text, start, end)
    except (ValueError, SyntaxError):
        return _fail("대상이 정적 리터럴이 아닙니다.")
    if not isinstance(old_value, (tuple, list)) or len(old_value) != 4:
        return _fail("대상이 길이 4 튜플이 아닙니다.")

    # 4. 새 값 구성 — None 인자는 기존 값 유지. 항상 튜플로 emit.
    old_type, old_aff, old_trust, old_status = old_value
    new_value = (
        rel_type if rel_type is not None else old_type,
        affinity if affinity is not None else old_aff,
        trust if trust is not None else old_trust,
        current_status if current_status is not None else old_status,
    )
    base_indent = _base_indent(text, node, line_offsets)
    new_src = _emit(new_value, base_indent)

    # 5~7. 치환 → 재파싱 → 재로케이트 후 의미 검증.
    return _apply_edit(
        path, text, new_src, start, end,
        expected=new_value,
        relocate=lambda t: _relocate_rel(t, source, target),
        message=f"{source}→{target} 관계를 갱신했습니다.",
    )


def edit_blob(world_id: str, char_id: str, role: str, props: dict, _label: str | None = None) -> dict:
    """role(static/personality/info) blob 의 insert_static_inline kwargs 를 props 로 전체 치환합니다.

    REL(3번째 인자)·LABEL(4번째)·node_id(5번째 f-string)는 원본 소스를 그대로 보존하고
    키워드 영역만 props 로 재생성합니다. 안전 절차를 모두 통과해야 기록합니다.
    _label 이 주어지면 _ROLE_LABEL 조회를 건너뜁니다 (커스텀 슬롯 용도).
    """
    if _label is not None:
        label = _label
    elif role not in _ROLE_LABEL:
        return _fail(f"알 수 없는 role: {role}")
    else:
        label = _ROLE_LABEL[role]

    path = find_character_file(world_id, char_id)
    if path is None:
        return _fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)

    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _fail("build_schema 메서드를 찾지 못했습니다.")

    call, reason = _find_blob_call(method, label)
    if call is None:
        return _fail(f"편집 불가: {reason}")

    # props 자체가 리터럴로 emit 가능한지 사전 검사.
    if not isinstance(props, dict) or not all(isinstance(k, str) for k in props):
        return _fail("props 는 str 키를 가진 dict 여야 합니다.")

    # Call 노드 전체 span 을 잡아 호출문을 통째로 재생성한다.
    start, end = _node_span(text, call, line_offsets)
    base_indent = _base_indent(text, call, line_offsets)

    # REL(args[2]), node_id(args[4]) 는 f-string 등 비리터럴일 수 있으므로
    # 원본 소스 세그먼트를 그대로 떼어 보존한다. LABEL 은 우리가 아는 상수.
    rel_arg = ast.get_source_segment(text, call.args[2])
    nodeid_arg = ast.get_source_segment(text, call.args[4])
    if rel_arg is None or nodeid_arg is None:
        return _fail("호출 인자 소스를 추출하지 못했습니다.")

    # 새 호출문 조립 — conn, self.id, "<REL>", "<LABEL>", <node_id>, key=val, ...
    inner = base_indent + "    "
    lines = ["insert_static_inline("]
    lines.append(f"{inner}conn, self.id, {rel_arg}, {repr(label)}, {nodeid_arg},")
    for k, v in props.items():
        lines.append(f"{inner}{k}={_emit(v, inner)},")
    lines.append(base_indent + ")")
    new_src = "\n".join(lines)

    return _apply_edit(
        path, text, new_src, start, end,
        expected=props,
        relocate=lambda t: _relocate_blob(t, char_id, label),
        message=f"{char_id} 의 {role} blob 을 갱신했습니다.",
    )


def edit_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict:
    """build_schema 내 현재 시나리오의 _state dict 를 fields 로 전체 치환합니다.

    무조건 직속 _state를 우선 사용하고, 없으면 정적 self.scenario_id 분기의 현재 branch를 사용합니다.
    """
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return _fail("fields 는 str 키를 가진 dict 여야 합니다.")
    fields = normalize_state_fields(fields)

    path = find_character_file(world_id, char_id)
    if path is None:
        return _fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)

    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _fail("build_schema 메서드를 찾지 못했습니다.")

    node, reason = _find_state_dict(method, scenario_id)
    if node is None:
        return _fail(f"편집 불가: {reason}")

    start, end = _node_span(text, node, line_offsets)
    base_indent = _base_indent(text, node, line_offsets)
    new_src = _emit(fields, base_indent)

    return _apply_edit(
        path, text, new_src, start, end,
        expected=fields,
        relocate=lambda t: _relocate_state(t, char_id, scenario_id),
        message=f"{char_id} 의 _state 를 갱신했습니다.",
    )


def edit_tuple_row(world_id: str, kind: str, row_id: str, values: dict) -> dict:
    """schema.py 의 튜플-행(첫 컬럼==row_id)을 템플릿 컬럼 순서로 재구성해 치환합니다.

    kind: "location" | "rule". arity(8)를 보존해야 하며, 비템플릿 모양이면 거부합니다.
    values 누락 컬럼은 기존 튜플 값을 유지합니다. 안전 절차를 모두 통과해야 기록합니다.
    """
    if kind not in _TUPLE_COLUMNS:
        return _fail(f"알 수 없는 kind: {kind}")
    columns = _TUPLE_COLUMNS[kind]

    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return _fail("schema.py 를 찾지 못했습니다.")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)

    node, reason = _find_tuple_row(tree, kind, row_id)
    if node is None:
        return _fail(f"편집 불가: {reason}")

    start, end = _node_span(text, node, line_offsets)

    # 현재 행을 literal_eval 로 읽어 누락 컬럼의 기존 값을 확보.
    try:
        old_row = _literal_eval_segment(text, start, end)
    except (ValueError, SyntaxError):
        return _fail("대상 행이 정적 리터럴이 아닙니다.")
    if len(old_row) != len(columns):
        return _fail("행 arity 가 템플릿과 다릅니다.")

    # values(컬럼명→값)를 템플릿 순서대로 적용. 누락 컬럼은 기존 값 유지.
    new_row = tuple(
        values[col] if col in values else old_row[i]
        for i, col in enumerate(columns)
    )
    base_indent = _base_indent(text, node, line_offsets)
    new_src = _emit(new_row, base_indent)

    return _apply_edit(
        path, text, new_src, start, end,
        expected=new_row,
        relocate=lambda t: _relocate_tuple_row(t, kind, row_id),
        message=f"{kind} '{row_id}' 행을 갱신했습니다.",
    )


def edit_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict:
    """캐릭터 파일의 insert_schedule 호출에서 정적 리터럴 kwargs 만 부분 치환합니다.

    schedule_id/owner_id 같은 식별자는 원본을 보존합니다. material/json.dumps, self.cfg,
    반복문 변수 등 계산식으로 된 값은 필드 단위로 거부합니다.
    """
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return _fail("fields 는 str 키를 가진 dict 여야 합니다.")

    path = find_character_file(world_id, char_id)
    if path is None:
        return _fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")

    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)

    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _fail("build_schema 메서드를 찾지 못했습니다.")

    call, reason = _find_schedule_call(method, char_id, schedule_id)
    if call is None:
        return _fail(f"편집 불가: {reason}")
    if any(kw.arg is None for kw in call.keywords):
        return _fail("편집 불가: **kwargs 를 사용하는 호출입니다.")

    kw_map = _call_kw_map(call)
    updates: dict[str, object] = {}
    for field, value in fields.items():
        if field not in _SCHEDULE_EDITABLE_FIELDS:
            continue
        source_key = _schedule_source_key(call, field)
        if source_key is None:
            return _fail(f"편집 불가: {field} kwarg 가 소스 호출에 없습니다.")
        if source_key not in kw_map or not _is_clean_literal_node(kw_map[source_key].value):
            return _fail(f"편집 불가: {source_key} 는 정적 리터럴이 아닙니다.")
        try:
            if source_key in {"day_of_week", "day_of_weeks"}:
                updates[source_key] = _coerce_weekday_set(value)
            elif source_key == "prompt_priority":
                updates[source_key] = int(value)
            elif source_key == "tags":
                updates[source_key] = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
            else:
                updates[source_key] = value
        except (TypeError, ValueError) as e:
            return _fail(f"{field} 값이 유효하지 않습니다: {e}")

    if not updates:
        return _fail("편집 가능한 schedule 필드가 없습니다.")

    edits: list[tuple[int, int, str]] = []
    for key, value in updates.items():
        value_node = kw_map[key].value
        start, end = _node_span(text, value_node, line_offsets)
        base_indent = _base_indent(text, value_node, line_offsets)
        edits.append((start, end, _emit_like_old(text[start:end], value, base_indent)))

    new_text = text
    for start, end, new_src in sorted(edits, reverse=True):
        new_text = _replace_node_span(new_text, start, end, new_src)

    try:
        new_tree = ast.parse(new_text)
    except SyntaxError as e:
        return _fail(f"치환 결과가 파싱되지 않습니다: {e}")
    try:
        actual = _relocate_schedule(new_tree, char_id, schedule_id, set(updates))
    except (ValueError, SyntaxError) as e:
        return _fail(f"치환 후 의미 검증 실패: {e}")
    if actual is _RELOCATE_MISS:
        return _fail("치환 후 대상 노드를 다시 찾지 못했습니다.")
    if not _semantic_equal(actual, updates):
        return _fail("치환 후 값이 의도와 일치하지 않습니다.")
    try:
        backup = _safe_write(path, new_text)
    except OSError as e:
        return _fail(f"파일 기록 실패: {e}")
    return _ok(f"{char_id} 의 schedule '{schedule_id}' 를 갱신했습니다.", backup)


_SCHEDULE_REWRITE_FIELDS: tuple[str, ...] = (
    "name", "activity", "summary", "prompt_hint", "prompt_priority", "material",
    "recurrence", "day_of_weeks", "date", "start_time", "end_time",
    "location_id", "status", "tags",
)


def rewrite_schedule_call(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict:
    """insert_schedule 호출 전체를 새 kwarg 집합으로 재작성합니다 (없던 필드도 추가 가능).

    conn / owner_id / schedule_id 식별 인자는 원본 소스 표현(self.id, f-string 등)을 그대로
    보존하고, 그 외 키워드는 fields 로 통째 교체합니다. edit_schedule 과 달리 소스 호출에
    없던 material/date/location/status 등도 새로 넣을 수 있습니다.
    """
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return _fail("fields 는 str 키를 가진 dict 여야 합니다.")
    path = find_character_file(world_id, char_id)
    if path is None:
        return _fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = _line_offsets(text)
    cls = _find_character_class(tree, char_id)
    method = _find_method(cls, "build_schema") if cls else None
    if method is None:
        return _fail("build_schema 메서드를 찾지 못했습니다.")
    call, reason = _find_schedule_call(method, char_id, schedule_id)
    if call is None:
        return _fail(f"편집 불가: {reason}")

    kw_map = _call_kw_map(call)

    def _src(node: ast.AST) -> str:
        start, end = _node_span(text, node, line_offsets)
        return text[start:end]

    conn_src = _src(call.args[0]) if call.args else "conn"
    owner_node = kw_map["owner_id"].value if "owner_id" in kw_map else (call.args[1] if len(call.args) > 1 else None)
    owner_src = _src(owner_node) if owner_node is not None else "self.id"
    sid_node = kw_map["schedule_id"].value if "schedule_id" in kw_map else (call.args[2] if len(call.args) > 2 else None)
    sid_src = _src(sid_node) if sid_node is not None else repr(schedule_id)

    call_start, call_end = _node_span(text, call, line_offsets)
    line_start = text.rfind("\n", 0, call_start) + 1
    base_indent = text[line_start:call_start]
    inner = base_indent + "    "

    parts = [conn_src, f"owner_id={owner_src}", f"schedule_id={sid_src}"]
    for key in _SCHEDULE_REWRITE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "prompt_priority":
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
        elif key == "day_of_weeks":
            value = sorted(_coerce_weekday_set(value))
        elif key == "tags":
            value = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        parts.append(f"{key}={_emit(value, inner)}")

    new_call = "insert_schedule(\n" + "".join(f"{inner}{p},\n" for p in parts) + base_indent + ")"
    new_text = _replace_node_span(text, call_start, call_end, new_call)
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return _fail(f"재작성 결과가 파싱되지 않습니다: {e}")
    try:
        backup = _safe_write(path, new_text)
    except OSError as e:
        return _fail(f"파일 기록 실패: {e}")
    return _ok(f"{char_id} 의 schedule '{schedule_id}' 를 재작성했습니다.", backup)


__all__ = [name for name in globals() if not name.startswith("__")]

