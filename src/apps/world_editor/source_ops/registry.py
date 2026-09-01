# ================================
# src/apps/world_editor/source_ops/registry.py
#
# Character registration and scenario character-list source operations.
#
# Functions
#   - register_character(world_id: str, class_name: str, char_id: str, char_type: str) -> dict : Register a character class.
#   - list_all_characters(world_id: str) -> list[dict] : List registered characters.
#   - get_scenario_characters(world_id: str, scenario_id: str | None) -> list[str] : Get scenario characters.
#   - set_scenario_characters(world_id: str, scenario_id: str | None, char_ids: list[str]) -> dict : Set scenario characters.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

def register_character(world_id: str, class_name: str, char_id: str, char_type: str) -> dict:
    """생성된 캐릭터를 characters/__init__.py 와 schema.py(import + chars 리스트 + narrator/pc)에 등록합니다."""
    pkg = world_pkg_dir(world_id)

    # 1. characters/__init__.py 에 export import 추가(중복 방지).
    init_path = pkg / "characters" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8") if init_path.exists() else '"""캐릭터 export."""\n'
    import_line = f"from .{char_id} import {class_name}\n"
    if import_line not in init_text:
        if not init_text.endswith("\n"):
            init_text += "\n"
        init_text += import_line
        try:
            ast.parse(init_text)
        except SyntaxError as e:
            return se._fail(f"characters/__init__.py 갱신 실패: {e}")
        se._safe_write(init_path, init_text)

    # 2. schema.py 수정 — import + chars 리스트 + (필요시) narrator/pc.
    schema_path = pkg / "schema.py"
    if not schema_path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = schema_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    world_call = _find_world_call(tree)
    if world_call is None:
        return se._fail("SCENARIOS 의 World(chars=[...]) 호출을 찾지 못했습니다.")
    chars_kw = next((k for k in world_call.keywords if k.arg == "chars"), None)
    if chars_kw is None or not isinstance(chars_kw.value, ast.List):
        return se._fail("chars=[...] 리스트를 찾지 못했습니다.")

    # 이미 등록돼 있으면(클래스 호출이 chars 에 있음) chars 삽입은 건너뛴다.
    already = any(isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == class_name
                  for e in chars_kw.value.elts)

    edits: list[tuple[int, int, str]] = []  # (start, end, new_src) — 우→좌 적용

    # 2a. import 라인 (마지막 최상위 import 뒤).
    import_stmt = f"from src.assets.worlds.{world_id}.characters import {class_name}\n"
    if import_stmt not in text:
        last_import = None
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                last_import = stmt
        pos = line_offsets[last_import.end_lineno + 1] if last_import and last_import.end_lineno + 1 < len(line_offsets) else 0
        edits.append((pos, pos, import_stmt))

    # 2b. chars 리스트에 ClassName() 추가 ('[' 직후 prepend).
    if not already:
        c_start, _c_end = se._node_span(text, chars_kw.value, line_offsets)
        edits.append((c_start + 1, c_start + 1, f"{class_name}(), "))

    # 2c. narrator/pc 가 None 이면 채운다(첫 캐릭터/첫 PC 기준).
    def _kw_none_span(arg_name: str):
        kw = next((k for k in world_call.keywords if k.arg == arg_name), None)
        if kw and isinstance(kw.value, ast.Constant) and kw.value.value is None:
            return se._node_span(text, kw.value, line_offsets)
        return None

    nar_span = _kw_none_span("narrator")
    if nar_span:
        edits.append((nar_span[0], nar_span[1], f"{class_name}()"))
    if char_type == "PC":
        pc_span = _kw_none_span("pc")
        if pc_span:
            edits.append((pc_span[0], pc_span[1], f"{class_name}()"))

    # 우→좌(시작 오프셋 내림차순)로 적용해 앞쪽 오프셋이 깨지지 않게 한다.
    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(schema_path, new_text)
    except OSError as e:
        return se._fail(f"schema.py 기록 실패: {e}")
    return se._ok(f"{class_name} 등록 완료.", backup)


def _find_world_call(tree: ast.Module) -> ast.Call | None:
    """SCENARIOS 의 World(chars=[...]) 생성 호출을 찾습니다.

    chars 의 '값이 리스트 리터럴'인 호출만 인정 — super().__init__(chars=chars or []) 처럼
    chars 가 리터럴이 아닌 호출(BoolOp/Name)은 제외해야 정확히 SCENARIOS 쪽을 잡는다.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for k in node.keywords:
                if k.arg == "chars" and isinstance(k.value, ast.List):
                    return node
    return None


def _class_str_attr(cls: ast.ClassDef, attr: str) -> str | None:
    """클래스 body 의 `attr = "..."` 문자열 리터럴 값을 추출합니다."""
    for stmt in cls.body:
        names, value = se._assign_target_names(stmt)
        if attr in names and value is not None:
            try:
                v = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
            return v if isinstance(v, str) else None
    return None


def list_all_characters(world_id: str) -> list[dict]:
    """월드에 정의된 모든 캐릭터를 반환합니다 (시나리오 무관). [{char_id, class_name, name}]."""
    pkg = world_pkg_dir(world_id)
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

    out: list[dict] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for cls in se._iter_classes(tree):
            cid = se._class_id_value(cls)
            if not cid or cid in seen:  # id 가 빈 문자열인 베이스 클래스 등은 제외
                continue
            seen.add(cid)
            out.append({"char_id": cid, "class_name": cls.name, "name": _class_str_attr(cls, "name") or cid})
    out.sort(key=lambda c: c["char_id"])
    return out


def _find_scenario_world_call(tree: ast.Module, scenario_id: str) -> ast.Call | None:
    """scenario_id 와 일치하는 Scenario(...) 의 world=World(...) 호출을 찾습니다.

    Scenario 호출은 scenario_id 와 world 키워드를 모두 가진다 — 이 둘로 식별한다.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        sid_kw = next((k for k in node.keywords if k.arg == "scenario_id"), None)
        world_kw = next((k for k in node.keywords if k.arg == "world"), None)
        if (sid_kw and world_kw and isinstance(sid_kw.value, ast.Constant)
                and sid_kw.value.value == scenario_id and isinstance(world_kw.value, ast.Call)):
            return world_kw.value
    return None


def _find_scenario_call(tree: ast.Module, scenario_id: str) -> ast.Call | None:
    """scenario_id 와 일치하는 Scenario(...) 호출을 찾습니다."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Scenario"):
            continue
        sid_kw = next((k for k in node.keywords if k.arg == "scenario_id"), None)
        if sid_kw and isinstance(sid_kw.value, ast.Constant) and sid_kw.value.value == scenario_id:
            return node
    return None


def _replace_or_insert_call_keyword(text: str, call: ast.Call, key: str, value: object) -> str:
    """Call 키워드 값을 치환하거나, 없으면 호출 끝에 새 키워드를 삽입한 소스를 반환합니다."""
    line_offsets = se._line_offsets(text)
    value_kw = next((kw for kw in call.keywords if kw.arg == key), None)
    if value_kw is not None:
        start, end = se._node_span(text, value_kw.value, line_offsets)
        base_indent = se._base_indent(text, value_kw.value, line_offsets)
        return se._replace_node_span(text, start, end, se._emit(value, base_indent))

    call_start, call_end = se._node_span(text, call, line_offsets)
    call_src = text[call_start:call_end]
    base_indent = se._base_indent(text, call, line_offsets)
    inner_indent = base_indent + "    "
    if "\n" in call_src:
        value_src = se._emit(value, inner_indent)
        insertion = f"{inner_indent}{key}={value_src},\n{base_indent}"
    else:
        value_src = se._emit(value, base_indent)
        insertion = f", {key}={value_src}"
    return se._replace_node_span(text, call_end - 1, call_end - 1, insertion)


def _scenario_chars_kw(tree: ast.Module, scenario_id: str | None) -> ast.keyword | None:
    """해당 시나리오 World 호출의 chars 키워드(리스트 값)를 반환합니다. 없으면 첫 World 호출로 폴백."""
    wc = _find_scenario_world_call(tree, scenario_id) if scenario_id else None
    if wc is None:
        wc = _find_world_call(tree)
    if wc is None:
        return None
    kw = next((k for k in wc.keywords if k.arg == "chars"), None)
    return kw if (kw and isinstance(kw.value, ast.List)) else None


def get_scenario_characters(world_id: str, scenario_id: str | None) -> list[str]:
    """해당 시나리오의 chars=[ClassName(), ...] 에서 char_id 목록을 추출합니다.

    schema.py 의 chars 리스트에 없는 캐릭터도 월드에 파일이 존재하면 목록 끝에 추가합니다.
    (새 캐릭터 추가 후 schema.py 를 수동 편집하지 않아도 오른쪽 패널과 중앙 패널이 동기화됩니다.)
    """
    all_chars = list_all_characters(world_id)
    all_cids = [c["char_id"] for c in all_chars]

    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return all_cids
    tree = ast.parse(path.read_text(encoding="utf-8"))
    kw = _scenario_chars_kw(tree, scenario_id)
    if kw is None:
        return all_cids
    cn_to_cid = {c["class_name"]: c["char_id"] for c in all_chars}
    out: list[str] = []
    for elt in kw.value.elts:  # type: ignore[attr-defined]
        if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name):
            cid = cn_to_cid.get(elt.func.id)
            if cid:
                out.append(cid)
    # schema.py 의 chars 에 없는 캐릭터를 순서 유지하며 뒤에 추가
    listed = set(out)
    for cid in all_cids:
        if cid not in listed:
            out.append(cid)
    return out


def _emit_chars_list(class_names: list[str], base_indent: str) -> str:
    """[ClassName(), ...] 소스를 생성합니다 (비면 [])."""
    if not class_names:
        return "[]"
    inner = base_indent + "    "
    lines = ["["]
    for cn in class_names:
        lines.append(f"{inner}{cn}(),")
    lines.append(base_indent + "]")
    return "\n".join(lines)


def set_scenario_characters(world_id: str, scenario_id: str | None, char_ids: list[str]) -> dict:
    """해당 시나리오의 chars 리스트를 char_ids 로 교체합니다(필요한 import 도 함께 보강)."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    kw = _scenario_chars_kw(tree, scenario_id)
    if kw is None:
        return se._fail("시나리오의 chars=[...] 리스트를 찾지 못했습니다.")

    cid_to_cn = {c["char_id"]: c["class_name"] for c in list_all_characters(world_id)}
    class_names: list[str] = []
    for cid in char_ids:
        cn = cid_to_cn.get(cid)
        if cn is None:
            return se._fail(f"알 수 없는 캐릭터: {cid}")
        class_names.append(cn)

    edits: list[tuple[int, int, str]] = []

    # 필요한 import 보강 (chars 에 쓰는 클래스가 schema 에 import 돼 있어야 컴파일됨).
    needed = [f"from src.assets.worlds.{world_id}.characters import {cn}\n"
              for cn in class_names if f"import {cn}\n" not in text and f"import {cn}" not in text]
    if needed:
        last_import = None
        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                last_import = stmt
        pos = line_offsets[last_import.end_lineno + 1] if last_import and last_import.end_lineno + 1 < len(line_offsets) else 0
        edits.append((pos, pos, "".join(needed)))

    # chars 리스트 노드 전체 교체.
    c_start, c_end = se._node_span(text, kw.value, line_offsets)
    base_indent = se._base_indent(text, kw.value, line_offsets)
    edits.append((c_start, c_end, _emit_chars_list(class_names, base_indent)))

    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id or 'default'}' 등장인물을 갱신했습니다 ({len(class_names)}명).", backup)

