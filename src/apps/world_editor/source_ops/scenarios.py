# ================================
# src/apps/world_editor/source_ops/scenarios.py
#
# Scenario creation, metadata, rename, scene-type, and perspective source operations.
#
# Functions
#   - create_scenario(world_id: str, scenario_id: str, display_name: str) -> dict : Create a scenario.
#   - update_scenario_meta(world_id: str, scenario_id: str, display_name: str) -> dict : Update scenario metadata.
#   - rename_scenario(world_id: str, old_scenario_id: str, new_scenario_id: str) -> dict : Rename a scenario.
#   - update_scene_types(world_id: str, scene_types: dict[str, str], scenario_id: str | None = None) -> dict : Update scene types.
#   - update_default_perspective(world_id: str, perspective: object, scenario_id: str | None = None) -> dict : Update default perspective.
# ================================

from __future__ import annotations

from src.apps.world_editor.source_ops.creator_support import *

def _find_world_class_name(tree: ast.Module) -> str | None:
    """SCENARIOS 의 World(chars=[...]) 호출에서 World 서브클래스명을 추출합니다."""
    wc = _find_world_call(tree)
    if wc is not None and isinstance(wc.func, ast.Name):
        return wc.func.id
    return None


def _migrate_legacy_scenarios(text: str) -> str | None:
    """레거시 world_instance 스타일 schema.py 에 SCENARIOS 리스트 뼈대를 삽입합니다.

    world_instance 대입 뒤에 SCENARIOS: list[Scenario] = [Scenario(world=world_instance)] 를 추가.
    Scenario import 가 없으면 함께 추가. 실패하면 None 반환.
    """
    tree = ast.parse(text)

    # world_instance 대입문 찾기
    world_inst_stmt: ast.stmt | None = None
    for stmt in tree.body:
        names, _ = se._assign_target_names(stmt)
        if "world_instance" in names:
            world_inst_stmt = stmt
            break
    if world_inst_stmt is None:
        return None

    line_offsets = se._line_offsets(text)
    _start, end = se._node_span(text, world_inst_stmt, line_offsets)

    scenarios_block = (
        "\n\nSCENARIOS: list[Scenario] = [\n"
        "    Scenario(\n"
        '        scenario_id="default",\n'
        '        display_name="기본",\n'
        "        world=world_instance,\n"
        "    ),\n"
        "]\n"
    )
    new_text = text[:end] + scenarios_block + text[end:]

    # Scenario import 보장
    new_text = _ensure_base_import(new_text, "Scenario")

    try:
        ast.parse(new_text)
    except SyntaxError:
        return None
    return new_text


def create_scenario(world_id: str, scenario_id: str, display_name: str) -> dict:
    """SCENARIOS 리스트에 빈 chars 의 새 Scenario(...) 항목을 추가합니다.

    SCENARIOS 리스트가 없는 레거시 세계관(world_instance 스타일)은 자동으로 SCENARIOS 뼈대를 삽입합니다.
    """
    if not _SID_RE.match(scenario_id or ""):
        return se._fail("scenario_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    # SCENARIOS = [...] 리스트 노드 찾기.
    scenarios_list: ast.List | None = None
    for stmt in tree.body:
        names, value = se._assign_target_names(stmt)
        if "SCENARIOS" in names and isinstance(value, ast.List):
            scenarios_list = value
            break

    # 레거시 world_instance 스타일이면 SCENARIOS 뼈대를 자동 삽입한다.
    if scenarios_list is None:
        migrated = _migrate_legacy_scenarios(text)
        if migrated is None:
            return se._fail(
                "SCENARIOS 리스트를 찾지 못했습니다. "
                "world_editor 로 생성된 세계관이거나 world_instance 가 있어야 합니다."
            )
        text = migrated
        tree = ast.parse(text)
        line_offsets = se._line_offsets(text)
        for stmt in tree.body:
            names, value = se._assign_target_names(stmt)
            if "SCENARIOS" in names and isinstance(value, ast.List):
                scenarios_list = value
                break
        if scenarios_list is None:
            return se._fail("SCENARIOS 마이그레이션 후 리스트를 찾지 못했습니다.")

    # 중복 scenario_id 방지 (_find_scenario_call 로 world=world_instance 스타일도 검출).
    if _find_scenario_call(tree, scenario_id) is not None:
        return se._fail(f"이미 존재하는 시나리오입니다: {scenario_id}")

    world_cls = _find_world_class_name(tree)
    if not world_cls:
        return se._fail("World 클래스명을 찾지 못했습니다.")

    display = (display_name or scenario_id).replace('"', "'")
    base_indent = se._base_indent(text, scenarios_list, line_offsets)
    inner = base_indent + "    "
    entry = (
        f"{inner}Scenario(\n"
        f'{inner}    scenario_id="{scenario_id}",\n'
        f'{inner}    display_name="{display}",\n'
        f"{inner}    world={world_cls}(\n"
        f"{inner}        narrator=None,\n"
        f"{inner}        pc=None,\n"
        f"{inner}        chars=[],\n"
        f'{inner}        scenario_id="{scenario_id}",\n'
        f"{inner}    ),\n"
        f"{inner}),\n"
    )
    # 리스트 닫는 ']' 직전에 삽입.
    # 기존 마지막 항목에 trailing comma 가 없으면 먼저 추가한다.
    _start, end = se._node_span(text, scenarios_list, line_offsets)
    insert_pos = end - 1
    prefix = text[:insert_pos].rstrip()
    if scenarios_list.elts and not prefix.endswith(","):
        # trailing comma 추가: rstrip 위치 바로 뒤에 ',' 삽입
        comma_pos = len(prefix)
        text = text[:comma_pos] + "," + text[comma_pos:]
        # 오프셋이 바뀌었으므로 재계산
        line_offsets = se._line_offsets(text)
        tree = ast.parse(text)
        for stmt in tree.body:
            names, value = se._assign_target_names(stmt)
            if "SCENARIOS" in names and isinstance(value, ast.List):
                scenarios_list = value
                break
        _start, end = se._node_span(text, scenarios_list, line_offsets)
        insert_pos = end - 1
    new_text = text[:insert_pos] + entry + text[insert_pos:]
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"시나리오 추가 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id}' 를 추가했습니다.", backup)


def update_scenario_meta(world_id: str, scenario_id: str, display_name: str) -> dict:
    """SCENARIOS 안의 Scenario(...).display_name 값을 갱신합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)

    call = _find_scenario_call(tree, scenario_id)
    if call is None:
        return se._fail(f"시나리오를 찾지 못했습니다: {scenario_id}")
    display_kw = next((k for k in call.keywords if k.arg == "display_name"), None)
    if display_kw is None or not isinstance(display_kw.value, ast.Constant):
        return se._fail("display_name 리터럴을 찾지 못했습니다. 소스에서 직접 편집하세요.")

    new_display = display_name.strip() or scenario_id
    start, end = se._node_span(text, display_kw.value, line_offsets)
    new_text = se._replace_node_span(text, start, end, repr(new_display))
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    return se._ok(f"시나리오 '{scenario_id}' 표시 이름을 갱신했습니다.", backup)


def _scenario_id_constant_edits(call: ast.Call, text: str, line_offsets: list[int], old_sid: str, new_sid: str) -> list[tuple[int, int, str]]:
    """Scenario(...) 하위 호출 안에서 old_sid 문자열 상수 교체 edit 목록을 만듭니다."""
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(call):
        if isinstance(node, ast.Constant) and node.value == old_sid:
            start, end = se._node_span(text, node, line_offsets)
            edits.append((start, end, repr(new_sid)))
    return edits


def _character_source_files(world_id: str) -> list[Path]:
    """월드 패키지에서 캐릭터 class가 있을 수 있는 Python 파일 목록을 반환합니다."""
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
    return candidates


def _rename_override_keys(path: Path, old_sid: str, new_sid: str) -> str | None:
    """파일 내 SCENARIO_OVERRIDES clean literal dict의 old_sid 키를 new_sid로 바꾼 새 텍스트를 반환합니다."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    line_offsets = se._line_offsets(text)
    edits: list[tuple[int, int, str]] = []
    for cls in se._iter_classes(tree):
        node = se._class_attr_node(cls, "SCENARIO_OVERRIDES")
        if node is None:
            continue
        if not isinstance(node, ast.Dict) or not se._is_clean_literal_node(node):
            continue
        overrides = ast.literal_eval(node)
        if not isinstance(overrides, dict) or old_sid not in overrides:
            continue
        if new_sid in overrides:
            raise ValueError(f"{path.name}: SCENARIO_OVERRIDES 에 이미 {new_sid!r} 키가 있습니다.")
        renamed = dict(overrides)
        renamed[new_sid] = renamed.pop(old_sid)
        start, end = se._node_span(text, node, line_offsets)
        base_indent = se._base_indent(text, node, line_offsets)
        edits.append((start, end, se._emit(renamed, base_indent)))
    if not edits:
        return None
    new_text = text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_text = new_text[:start] + src + new_text[end:]
    ast.parse(new_text)
    return new_text


def rename_scenario(world_id: str, old_scenario_id: str, new_scenario_id: str) -> dict:
    """시나리오 id를 schema.py, prompt/scenarios 폴더, 캐릭터 override key에서 함께 변경합니다."""
    if not _SID_RE.match(new_scenario_id or ""):
        return se._fail("new_scenario_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    if old_scenario_id == new_scenario_id:
        return se._fail("새 scenario_id 가 기존 값과 같습니다.")

    pkg = world_pkg_dir(world_id)
    schema_path = pkg / "schema.py"
    if not schema_path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")

    prompt_root = pkg / "prompt" / "scenarios"
    old_prompt_dir = prompt_root / old_scenario_id
    new_prompt_dir = prompt_root / new_scenario_id
    if new_prompt_dir.exists():
        return se._fail(f"대상 prompt/scenarios 폴더가 이미 있습니다: {new_scenario_id}")

    schema_text = schema_path.read_text(encoding="utf-8")
    schema_tree = ast.parse(schema_text)
    if _find_scenario_call(schema_tree, new_scenario_id) is not None:
        return se._fail(f"이미 존재하는 시나리오입니다: {new_scenario_id}")
    call = _find_scenario_call(schema_tree, old_scenario_id)
    if call is None:
        return se._fail(f"시나리오를 찾지 못했습니다: {old_scenario_id}")

    line_offsets = se._line_offsets(schema_text)
    edits = _scenario_id_constant_edits(call, schema_text, line_offsets, old_scenario_id, new_scenario_id)
    if not edits:
        return se._fail("변경할 scenario_id 리터럴을 찾지 못했습니다.")
    new_schema_text = schema_text
    for start, end, src in sorted(edits, key=lambda e: e[0], reverse=True):
        new_schema_text = new_schema_text[:start] + src + new_schema_text[end:]
    try:
        ast.parse(new_schema_text)
    except SyntaxError as e:
        return se._fail(f"schema.py 갱신 결과가 파싱되지 않습니다: {e}")

    char_texts: list[tuple[Path, str]] = []
    try:
        for path in _character_source_files(world_id):
            if path == schema_path:
                continue
            renamed = _rename_override_keys(path, old_scenario_id, new_scenario_id)
            if renamed is not None:
                char_texts.append((path, renamed))
    except (OSError, SyntaxError, ValueError) as e:
        return se._fail(f"캐릭터 override key 갱신 준비 실패: {e}")

    backups: list[str] = []
    try:
        backups.append(se._safe_write(schema_path, new_schema_text))
        for path, new_text in char_texts:
            backups.append(se._safe_write(path, new_text))
        if old_prompt_dir.exists():
            old_prompt_dir.rename(new_prompt_dir)
    except OSError as e:
        return se._fail(f"scenario_id 변경 중 파일 기록 실패: {e}")

    return {
        "ok": True,
        "message": f"시나리오 id를 '{old_scenario_id}'에서 '{new_scenario_id}'로 변경했습니다.",
        "backup": "; ".join(backups),
        "formatted": True,
    }


def update_scene_types(world_id: str, scene_types: dict[str, str], scenario_id: str | None = None) -> dict:
    """World 클래스 또는 Scenario.scene_types dict 리터럴을 치환합니다."""
    if not isinstance(scene_types, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in scene_types.items()):
        return se._fail("scene_types 는 str→str dict 여야 합니다.")

    source = _load_schema_source(world_id)
    if isinstance(source, dict):
        return source

    if scenario_id:
        new_text = _replace_scenario_keyword(source, scenario_id, "scene_types", scene_types)
        if isinstance(new_text, dict):
            return new_text
        return _write_schema_source(source, new_text, f"시나리오 '{scenario_id}' scene_types 를 갱신했습니다.")

    cls = _find_world_class(source.tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")
    new_text = _replace_class_attr_literal(source, cls, "SCENE_TYPES", scene_types)
    if isinstance(new_text, dict):
        return new_text
    new_text = _remove_init_scene_types_override(new_text)
    if isinstance(new_text, dict):
        return new_text
    return _write_schema_source(source, new_text, "SCENE_TYPES 를 갱신했습니다.")


def update_default_perspective(world_id: str, perspective: object, scenario_id: str | None = None) -> dict:
    """World 클래스 또는 시나리오 World(...)의 perspective 리터럴을 치환합니다."""
    if not (
        isinstance(perspective, int)
        or (
            isinstance(perspective, list)
            and len(perspective) in (2, 3)
            and isinstance(perspective[0], int)
            and isinstance(perspective[1], str)
            and (len(perspective) == 2 or isinstance(perspective[2], bool))
        )
    ):
        return se._fail("perspective 는 정수, [정수, 문자열], 또는 [정수, 문자열, 불리언] 이어야 합니다.")
    value: object = tuple(perspective) if isinstance(perspective, list) else perspective

    source = _load_schema_source(world_id)
    if isinstance(source, dict):
        return source

    if scenario_id:
        new_text = _replace_scenario_world_keyword(source, scenario_id, "perspective", value)
        if isinstance(new_text, dict):
            return new_text
        return _write_schema_source(source, new_text, f"시나리오 '{scenario_id}' perspective 를 갱신했습니다.")

    cls = _find_world_class(source.tree)
    if cls is None:
        return se._fail("World 클래스를 찾지 못했습니다.")
    new_text = _replace_class_attr_literal(source, cls, "DEFAULT_PERSPECTIVE", value)
    if isinstance(new_text, dict):
        return new_text
    return _write_schema_source(source, new_text, "DEFAULT_PERSPECTIVE 를 갱신했습니다.")

