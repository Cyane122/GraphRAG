# ================================
# src/apps/world_editor/repair.py
#
# World Editor가 자동 편집하지 못하는 소스 구조를 진단하고 제한적으로 템플릿 변환합니다.
#
# Functions
#   - build_repair_report(world_id: str, scenario_id: str | None, graph: dict) -> dict : 편집 차단/복구 후보를 반환합니다.
#   - repair_issue(world_id: str, scenario_id: str | None, graph: dict, issue_type: str, scope: str, target: str, apply: bool = False) -> dict : 단일 복구 후보의 diff/apply를 수행합니다.
# ================================

from __future__ import annotations

import ast
import shutil
from collections import Counter
from difflib import unified_diff
from pathlib import Path
from typing import Callable

from src.apps.world_editor import compiler, migrate, scaffold, source_create, source_edit as se
from src.apps.world_editor.worlds import world_pkg_dir

_ROLE_BY_SCOPE: dict[str, str] = {
    "character.static": "static",
    "character.personality": "personality",
    "character.info": "info",
}

_LABEL_BY_ROLE: dict[str, str] = {
    "static": "StaticProfile",
    "personality": "Personality",
    "info": "DynamicInformation",
}

RepairIssue = dict[str, str | bool]


def _issue(
    issue_type: str,
    scope: str,
    target: str,
    reason: str,
    action: str,
    repairable: bool = False,
) -> RepairIssue:
    """진단 항목 dict를 생성합니다."""
    return {
        "type": issue_type,
        "scope": scope,
        "target": target,
        "reason": reason,
        "action": action,
        "repairable": repairable,
    }


def _character_issues(world_id: str, graph: dict) -> list[RepairIssue]:
    """캐릭터/관계/schedule 편집 차단 사유를 리포트 항목으로 변환합니다."""
    issues: list[RepairIssue] = []
    for char in graph.get("characters", []):
        cid = str(char.get("id") or "")
        source_file = char.get("source_file")
        if not source_file:
            # 단일 함수-직속 inline CREATE 면 자동 분리 가능 → repairable. 아니면 정밀 사유를 action 에.
            repairable = _can_create_character_source(world_id, char)
            if repairable:
                action = "schema.py inline CREATE 1건을 제거하고 클래스 파일로 분리합니다 (재컴파일로 동일성 검증)."
            else:
                _stmt, action = _check_inline_create(world_id, cid)
                action = action or "자동 분리 조건을 충족하지 않습니다 (소스에서 직접 정리하세요)."
            issues.append(_issue(
                "missing_character_source",
                "character",
                cid,
                "캐릭터 소스 파일을 찾지 못했습니다.",
                action,
                repairable,
            ))

        for role, meta in (char.get("edit") or {}).items():
            reason = str((meta or {}).get("reason") or "")
            editable = bool((meta or {}).get("editable"))
            if editable or not reason:
                continue
            issue_type = "scenario_conditional_state" if role == "state" and "scenario-conditional" in reason else "computed_blob"
            action = (
                "정적 분석 가능한 scenario_id 분기만 State 편집 대상으로 확장합니다."
                if issue_type == "scenario_conditional_state"
                else "computed/spread 호출은 clean literal template로 변환 가능한 경우만 마이그레이션합니다."
            )
            issues.append(_issue(
                issue_type,
                f"character.{role}",
                cid,
                _ko_reason(reason),
                action,
                _can_repair_blob(world_id, cid, f"character.{role}", char.get(role, {}), reason),
            ))

        for schedule in char.get("schedules", []):
            edit = schedule.get("edit") or {}
            if edit.get("editable"):
                continue
            reason = str(edit.get("reason") or "")
            if not reason:
                continue
            sid = str(schedule.get("id") or "")
            issues.append(_issue(
                "computed_schedule",
                "schedule",
                f"{cid}:{sid}",
                _ko_reason(reason),
                "owner_id=self.id 인 clean literal insert_schedule 호출만 자동 편집합니다.",
                _can_repair_schedule(world_id, cid, sid, reason),
            ))
    return issues


def _tuple_issues(world_id: str, graph: dict) -> list[RepairIssue]:
    """location/rule 튜플 행의 non-template 상태를 리포트 항목으로 변환합니다."""
    issues: list[RepairIssue] = []
    for kind, key in (("location", "locations"), ("rule", "rules")):
        for row in graph.get(key, []):
            if row.get("editable"):
                continue
            reason = str(row.get("reason") or "")
            if "non-template shape" not in reason:
                continue
            row_id = str(row.get("id") or row.get("rule_id") or "")
            issues.append(_issue(
                "non_template_tuple",
                kind,
                row_id,
                _ko_reason(reason),
                "인식 가능한 list[tuple] holder만 8-column template로 변환합니다.",
                _can_repair_tuple(world_id, kind, row_id),
            ))
    return issues


def _relationship_issues(graph: dict) -> list[RepairIssue]:
    """관계 편집 차단 사유를 리포트 항목으로 변환합니다."""
    issues: list[RepairIssue] = []
    for rel in graph.get("relationships", []):
        if rel.get("editable"):
            continue
        reason = str(rel.get("reason") or "")
        if not reason:
            continue
        target = f"{rel.get('source', '')}->{rel.get('target', '')}"
        issues.append(_issue(
            "non_template_relationship",
            "relationship",
            target,
            _ko_reason(reason),
            "관계 dict가 clean literal 4-tuple이면 자동 편집하고, 중복/계산식은 소스 편집으로 유지합니다.",
            False,
        ))
    return issues


def _ko_reason(reason: str) -> str:
    """소스 분석 사유를 UI용 한국어 문구로 정규화합니다."""
    mapping = {
        "character file not found": "캐릭터 소스 파일을 찾지 못했습니다.",
        "source character file not found": "관계 source 캐릭터 파일을 찾지 못했습니다.",
        "build_relationship not found": "build_relationship 메서드를 찾지 못했습니다.",
        "build_schema not found": "build_schema 메서드를 찾지 못했습니다.",
        "scenario-conditional state; edit in source": "시나리오 분기형 state라 소스 편집이 필요합니다.",
        "uses computed/spread values; edit in source": "계산식 또는 **spread 값을 사용해 소스 편집이 필요합니다.",
        "matching insert_schedule call is computed or shared; edit in source": "schedule 호출이 계산식이거나 여러 owner가 공유해 소스 편집이 필요합니다.",
        "no literal editable kwargs": "정적 리터럴로 편집 가능한 schedule 필드가 없습니다.",
        "non-template shape; edit in source": "템플릿 튜플 모양이 아니라 소스 편집이 필요합니다.",
    }
    return mapping.get(reason, reason)


def build_repair_report(world_id: str, scenario_id: str | None, graph: dict) -> dict:
    """편집 차단 지점과 제한적 복구 후보를 반환합니다."""
    issues: list[RepairIssue] = []
    issues.extend(_character_issues(world_id, graph))
    issues.extend(_tuple_issues(world_id, graph))
    issues.extend(_relationship_issues(graph))

    counts = Counter(item["type"] for item in issues)
    return {
        "world_id": world_id,
        "scenario_id": scenario_id or "default",
        "summary": dict(sorted(counts.items())),
        "issues": issues,
        "all_characters": source_create.list_all_characters(world_id),
    }


def _can_repair_tuple(world_id: str, kind: str, row_id: str) -> bool:
    """표준보다 짧은 clean tuple row가 schema.py 안에 단일하게 있는지 반환합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or not node.elts:
            continue
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value == row_id and se._is_clean_literal_node(node):
            try:
                value = ast.literal_eval(node)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, tuple) and len(value) < len(se._TUPLE_COLUMNS[kind]):
                matches.append(node)
    return len(matches) == 1


def _can_repair_blob(world_id: str, char_id: str, scope: str, props: object, reason: str) -> bool:
    """computed/spread blob 호출을 단일 clean literal 호출로 변환 가능한지 반환합니다."""
    role = _ROLE_BY_SCOPE.get(scope)
    if role is None or "computed/spread" not in reason or not isinstance(props, dict) or not props:
        return False
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return False
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return False
    return _find_static_call_any(method, _LABEL_BY_ROLE[role]) is not None


def _can_repair_schedule(world_id: str, char_id: str, schedule_id: str, reason: str) -> bool:
    """computed/shared schedule 호출이 단일 self.id 호출로 변환 가능한지 반환합니다."""
    if "computed" not in reason and "no literal editable kwargs" not in reason:
        return False
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return False
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return False
    return _find_schedule_call_any(method, char_id, schedule_id) is not None


def repair_issue(
    world_id: str,
    scenario_id: str | None,
    graph: dict,
    issue_type: str,
    scope: str,
    target: str,
    apply: bool = False,
) -> dict:
    """단일 repair report 항목을 preview 하거나 실제 적용합니다."""
    if issue_type == "non_template_tuple" and scope in {"location", "rule"}:
        return _repair_tuple(world_id, scope, target, apply)
    if issue_type == "computed_blob" and scope in _ROLE_BY_SCOPE:
        role = _ROLE_BY_SCOPE[scope]
        char = _find_character(graph, target)
        return _repair_blob(world_id, target, role, char.get(role, {}), apply)
    if issue_type == "computed_schedule" and scope == "schedule":
        char_id, schedule_id = _split_schedule_target(target)
        schedule = _find_schedule(graph, char_id, schedule_id)
        return _repair_schedule(world_id, char_id, schedule_id, schedule, apply)
    if issue_type == "missing_character_source":
        char = _find_character(graph, target)
        return _repair_missing_character_source(world_id, char, apply)
    return se._fail("이 항목은 자동 복구를 지원하지 않습니다.")


def _find_character(graph: dict, char_id: str) -> dict:
    """그래프에서 char_id에 해당하는 캐릭터 dict를 반환합니다."""
    for char in graph.get("characters", []):
        if char.get("id") == char_id:
            return char
    raise ValueError(f"캐릭터를 찾지 못했습니다: {char_id}")


def _split_schedule_target(target: str) -> tuple[str, str]:
    """repair target 문자열을 (char_id, schedule_id)로 분리합니다."""
    if ":" not in target:
        raise ValueError("schedule target 은 'char_id:schedule_id' 형식이어야 합니다.")
    char_id, schedule_id = target.split(":", 1)
    return char_id, schedule_id


def _find_schedule(graph: dict, char_id: str, schedule_id: str) -> dict:
    """그래프에서 캐릭터 schedule dict를 반환합니다."""
    char = _find_character(graph, char_id)
    for schedule in char.get("schedules", []):
        if schedule.get("id") == schedule_id:
            return schedule
    raise ValueError(f"schedule 을 찾지 못했습니다: {char_id}:{schedule_id}")


def _diff_result(
    path: Path,
    old_text: str,
    new_text: str,
    message: str,
    apply: bool,
    validator: Callable[[ast.Module], str | None] | None = None,
    world_id: str | None = None,
) -> dict:
    """변환 결과를 검증한 뒤 unified diff로 반환하고 apply=True면 파일에 저장합니다.

    world_id 가 주어지면 적용 후 재컴파일까지 검증해, 월드 빌드를 깨는 변경이면 되돌립니다.
    """
    try:
        tree = ast.parse(new_text)
        compile(tree, str(path), "exec")
    except SyntaxError as e:
        return se._fail(f"치환 결과가 파싱되지 않습니다: {e}")
    except (TypeError, ValueError) as e:
        return se._fail(f"치환 결과가 컴파일되지 않습니다: {e}")
    if validator is not None:
        error = validator(tree)
        if error:
            return se._fail(error)
    diff = "".join(unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"{path.as_posix()} (before)",
        tofile=f"{path.as_posix()} (after)",
    ))
    if not diff:
        return {"ok": True, "message": "변경할 내용이 없습니다.", "diff": "", "backup": None, "formatted": False}
    if not apply:
        return {"ok": True, "message": message, "diff": diff, "backup": None, "formatted": True}
    try:
        backup = se._safe_write(path, new_text)
    except OSError as e:
        return se._fail(f"파일 기록 실패: {e}")
    # verify-by-recompile: 적용 결과가 월드 빌드를 깨면 즉시 .bak 으로 롤백하고 거부한다.
    # (커스텀 arity 튜플을 표준 템플릿으로 잘못 패딩하는 등 AST 검증만으론 못 잡는 사고 차단.)
    # 시나리오별 빌드 경로(예: volleyball_team 전용 위치)도 깨질 수 있으므로 모든 시나리오를 검증한다.
    if world_id:
        try:
            compiler.invalidate(world_id)
            for sid in (migrate._scenario_ids(world_id) or [None]):
                compiler.compile_world_graph(world_id, sid, use_cache=False)
        except Exception as e:
            shutil.copy2(backup, path)
            compiler.invalidate(world_id)
            return se._fail(f"적용 후 재컴파일에 실패해 되돌렸습니다: {e}")
    return {"ok": True, "message": message, "diff": diff, "backup": backup, "formatted": True}


def _validate_tuple_repair(kind: str, row_id: str, expected: tuple) -> Callable[[ast.Module], str | None]:
    """tuple 변환 후 해당 row가 정확히 하나의 clean template tuple인지 검사하는 함수를 만듭니다."""
    columns = se._TUPLE_COLUMNS[kind]

    def _validator(tree: ast.Module) -> str | None:
        """변환된 AST에서 대상 tuple row의 arity와 literal 값을 검증합니다."""
        matches: list[ast.Tuple] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Tuple) or not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == row_id:
                matches.append(node)
        if len(matches) != 1:
            return "검증 실패: 대상 tuple row가 단일 항목이 아닙니다."
        node = matches[0]
        if not se._is_clean_literal_node(node):
            return "검증 실패: 대상 tuple row가 clean literal 이 아닙니다."
        try:
            value = ast.literal_eval(node)
        except (ValueError, SyntaxError, TypeError) as e:
            return f"검증 실패: tuple literal 평가 실패: {e}"
        if not isinstance(value, tuple) or len(value) != len(columns):
            return "검증 실패: 대상 tuple row가 표준 템플릿 arity가 아닙니다."
        if value != expected:
            return "검증 실패: 대상 tuple row 값이 preview 결과와 다릅니다."
        return None

    return _validator


def _validate_blob_repair(char_id: str, label: str, props: dict) -> Callable[[ast.Module], str | None]:
    """blob 변환 후 대상 insert_static_inline 호출 kwargs가 clean literal인지 검사하는 함수를 만듭니다."""

    def _validator(tree: ast.Module) -> str | None:
        """변환된 AST에서 대상 blob 호출의 키/값을 검증합니다."""
        cls = se._find_character_class(tree, char_id)
        method = se._find_method(cls, "build_schema") if cls else None
        if method is None:
            return "검증 실패: build_schema 메서드를 찾지 못했습니다."
        call = _find_static_call_any(method, label)
        if call is None:
            return f"검증 실패: {label} insert_static_inline 호출이 단일 항목이 아닙니다."
        kw_map = se._call_kw_map(call)
        for key, expected in props.items():
            keyword = kw_map.get(key)
            if keyword is None:
                return f"검증 실패: {key} kwarg 가 변환 결과에 없습니다."
            if not se._is_clean_literal_node(keyword.value):
                return f"검증 실패: {key} 값이 clean literal 이 아닙니다."
            try:
                actual = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError, TypeError) as e:
                return f"검증 실패: {key} literal 평가 실패: {e}"
            if actual != expected:
                return f"검증 실패: {key} 값이 컴파일된 그래프 값과 다릅니다."
        return None

    return _validator


def _validate_schedule_repair(
    char_id: str,
    schedule_id: str,
    fields: dict,
) -> Callable[[ast.Module], str | None]:
    """schedule 변환 후 대상 insert_schedule 호출 kwargs가 clean literal인지 검사하는 함수를 만듭니다."""

    def _validator(tree: ast.Module) -> str | None:
        """변환된 AST에서 대상 schedule 호출의 owner/schedule/kwargs를 검증합니다."""
        cls = se._find_character_class(tree, char_id)
        method = se._find_method(cls, "build_schema") if cls else None
        if method is None:
            return "검증 실패: build_schema 메서드를 찾지 못했습니다."
        call = _find_schedule_call_any(method, char_id, schedule_id)
        if call is None:
            return "검증 실패: owner_id=self.id 인 대상 schedule 호출이 단일 항목이 아닙니다."
        kw_map = se._call_kw_map(call)
        for key, expected in fields.items():
            keyword = kw_map.get(key)
            if keyword is None:
                return f"검증 실패: {key} kwarg 가 변환 결과에 없습니다."
            if not se._is_clean_literal_node(keyword.value):
                return f"검증 실패: {key} 값이 clean literal 이 아닙니다."
            try:
                actual = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError, TypeError) as e:
                return f"검증 실패: {key} literal 평가 실패: {e}"
            if actual != expected:
                return f"검증 실패: {key} 값이 컴파일된 그래프 값과 다릅니다."
        return None

    return _validator


def _repair_tuple(world_id: str, kind: str, row_id: str, apply: bool) -> dict:
    """arity가 다른 tuple row를 표준 8-column tuple로 변환합니다."""
    path = world_pkg_dir(world_id) / "schema.py"
    if not path.is_file():
        return se._fail("schema.py 를 찾지 못했습니다.")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    columns = se._TUPLE_COLUMNS[kind]
    line_offsets = se._line_offsets(text)
    matches: list[ast.Tuple] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple) or not node.elts:
            continue
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value == row_id and se._is_clean_literal_node(node):
            matches.append(node)
    if len(matches) != 1:
        return se._fail("복구 가능한 단일 tuple row를 찾지 못했습니다.")
    old_row = ast.literal_eval(matches[0])
    if not isinstance(old_row, tuple) or len(old_row) >= len(columns):
        return se._fail("표준 템플릿보다 짧은 tuple row만 자동 변환합니다.")
    defaults: dict[str, object] = {"prompt_priority": 0, "tags": [], "links": [], "scenarios": []}
    new_row = tuple(old_row[i] if i < len(old_row) else defaults.get(col, "") for i, col in enumerate(columns))
    start, end = se._node_span(text, matches[0], line_offsets)
    new_src = se._emit(new_row, se._base_indent(text, matches[0], line_offsets))
    new_text = se._replace_node_span(text, start, end, new_src)
    return _diff_result(
        path,
        text,
        new_text,
        f"{kind} '{row_id}' 행을 표준 템플릿으로 변환했습니다.",
        apply,
        _validate_tuple_repair(kind, row_id, new_row),
        world_id=world_id,
    )


def _repair_blob(world_id: str, char_id: str, role: str, props: dict, apply: bool) -> dict:
    """computed/spread insert_static_inline 호출을 clean literal kwargs 호출로 변환합니다."""
    if not isinstance(props, dict) or not props:
        return se._fail("컴파일된 blob props가 비어 있어 자동 변환하지 않습니다.")
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return se._fail("build_schema 메서드를 찾지 못했습니다.")
    label = _LABEL_BY_ROLE[role]
    call = _find_static_call_any(method, label)
    if call is None or len(call.args) < 5:
        return se._fail(f"{label} insert_static_inline 호출을 찾지 못했습니다.")
    rel_arg = ast.get_source_segment(text, call.args[2])
    nodeid_arg = ast.get_source_segment(text, call.args[4])
    if rel_arg is None or nodeid_arg is None:
        return se._fail("호출 인자 소스를 추출하지 못했습니다.")
    line_offsets = se._line_offsets(text)
    base_indent = se._base_indent(text, call, line_offsets)
    inner = base_indent + "    "
    lines = ["insert_static_inline("]
    lines.append(f"{inner}conn, self.id, {rel_arg}, {label!r}, {nodeid_arg},")
    for key, value in props.items():
        if not key.isidentifier():
            return se._fail(f"키워드 인자로 쓸 수 없는 blob key 입니다: {key}")
        lines.append(f"{inner}{key}={se._emit(value, inner)},")
    lines.append(base_indent + ")")
    start, end = se._node_span(text, call, line_offsets)
    new_text = se._replace_node_span(text, start, end, "\n".join(lines))
    return _diff_result(
        path,
        text,
        new_text,
        f"{char_id} 의 {role} blob 을 clean literal template로 변환했습니다.",
        apply,
        _validate_blob_repair(char_id, label, props),
        world_id=world_id,
    )


def _find_static_call_any(method: ast.FunctionDef, label: str) -> ast.Call | None:
    """리터럴 여부와 무관하게 label에 대응하는 insert_static_inline 호출을 찾습니다."""
    matches: list[ast.Call] = []
    for call in ast.walk(method):
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Name) and call.func.id == "insert_static_inline"):
            continue
        if len(call.args) >= 4 and isinstance(call.args[3], ast.Constant) and call.args[3].value == label:
            matches.append(call)
    return matches[0] if len(matches) == 1 else None


def _repair_schedule(world_id: str, char_id: str, schedule_id: str, schedule: dict, apply: bool) -> dict:
    """computed insert_schedule 호출을 컴파일된 값 기반 clean literal kwargs 호출로 변환합니다."""
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return se._fail(f"캐릭터 파일을 찾지 못했습니다: {char_id}")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = se._find_character_class(tree, char_id)
    method = se._find_method(cls, "build_schema") if cls else None
    if method is None:
        return se._fail("build_schema 메서드를 찾지 못했습니다.")
    call = _find_schedule_call_any(method, char_id, schedule_id)
    if call is None:
        return se._fail("owner_id=self.id 인 단일 insert_schedule 호출만 자동 변환합니다.")
    if len(call.args) < 3:
        return se._fail("conn, owner_id, schedule_id 위치 인자를 보존할 수 없습니다.")
    conn_arg = ast.get_source_segment(text, call.args[0])
    owner_arg = ast.get_source_segment(text, call.args[1])
    schedule_arg = ast.get_source_segment(text, call.args[2])
    if conn_arg is None or owner_arg is None or schedule_arg is None:
        return se._fail("schedule 호출 인자 소스를 추출하지 못했습니다.")
    fields = {
        key: schedule[key]
        for key in se._SCHEDULE_EDITABLE_FIELDS
        if key in schedule and schedule[key] is not None
    }
    if not fields:
        return se._fail("컴파일된 schedule 값이 비어 있어 자동 변환하지 않습니다.")
    line_offsets = se._line_offsets(text)
    base_indent = se._base_indent(text, call, line_offsets)
    inner = base_indent + "    "
    lines = ["insert_schedule("]
    lines.append(f"{inner}{conn_arg}, {owner_arg}, {schedule_arg},")
    for key, value in fields.items():
        lines.append(f"{inner}{key}={se._emit(value, inner)},")
    lines.append(base_indent + ")")
    start, end = se._node_span(text, call, line_offsets)
    new_text = se._replace_node_span(text, start, end, "\n".join(lines))
    return _diff_result(
        path,
        text,
        new_text,
        f"{char_id}:{schedule_id} schedule 을 clean literal template로 변환했습니다.",
        apply,
        _validate_schedule_repair(char_id, schedule_id, fields),
        world_id=world_id,
    )


def _find_schedule_call_any(method: ast.FunctionDef, char_id: str, schedule_id: str) -> ast.Call | None:
    """리터럴 여부와 무관하게 owner_id=self.id, schedule_id 일치 호출을 찾습니다."""
    matches: list[ast.Call] = []
    for call in ast.walk(method):
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Name) and call.func.id == "insert_schedule"):
            continue
        kw_map = se._call_kw_map(call)
        owner_node = kw_map["owner_id"].value if "owner_id" in kw_map else (call.args[1] if len(call.args) > 1 else None)
        if not se._is_self_id(owner_node):
            continue
        schedule_node = kw_map["schedule_id"].value if "schedule_id" in kw_map else (call.args[2] if len(call.args) > 2 else None)
        if se._eval_schedule_id_expr(schedule_node, char_id) == schedule_id:
            matches.append(call)
    return matches[0] if len(matches) == 1 else None


def _char_create_mentions_id(call: ast.Call, char_id: str) -> bool:
    """conn.execute(...) Character CREATE 호출이 이 char_id 를 리터럴로 지정하는지 판정합니다.

    SQL 문자열의 'id' 리터럴, 또는 두 번째 인자 params dict 값에서 char_id 를 찾습니다.
    루프 변수(`{"id": cid}`)처럼 계산식으로 만든 노드는 매칭되지 않습니다(보수적).
    """
    sql = migrate._execute_sql(call)
    if f"'{char_id}'" in sql or f'"{char_id}"' in sql:
        return True
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Dict):
        for value in call.args[1].values:
            if isinstance(value, ast.Constant) and value.value == char_id:
                return True
    return False


def _is_char_create_for(stmt: ast.stmt, char_id: str) -> bool:
    """stmt 가 char_id Character 노드를 만드는 inline CREATE 문장인지 판정합니다."""
    if not migrate._is_character_create_stmt(stmt):
        return False
    return _char_create_mentions_id(stmt.value, char_id)  # _is_character_create_stmt 가 Expr(Call) 보장


def _find_inline_char_creates(tree: ast.Module, char_id: str) -> tuple[list[ast.stmt], int]:
    """schema.py 에서 char_id inline CREATE 를 찾습니다.

    반환 (direct, total): direct 는 함수 본문 '직속' 문장(안전 제거 대상),
    total 은 루프/분기 내부 포함 전체 매치 수. total==1 이고 len(direct)==1 일 때만 안전.
    """
    direct: list[ast.stmt] = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for stmt in fn.body:
                if _is_char_create_for(stmt, char_id):
                    direct.append(stmt)
    total = sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.stmt) and _is_char_create_for(node, char_id)
    )
    return direct, total


def _check_inline_create(world_id: str, char_id: str) -> tuple[ast.stmt | None, str]:
    """schema.py 의 char_id inline CREATE 안전성을 판정합니다.

    안전하면 (stmt, ""), 아니면 (None, 정밀 사유). 단일 함수-직속 CREATE 만 안전(막연한 거부 금지).
    """
    schema_path = world_pkg_dir(world_id) / "schema.py"
    if not schema_path.is_file():
        return None, "schema.py 를 찾지 못했습니다."
    try:
        tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        return None, f"schema.py 파싱 실패: {e}"
    direct, total = _find_inline_char_creates(tree, char_id)
    if total == 0:
        return None, f"schema.py 에서 '{char_id}' 의 inline CREATE 를 찾지 못했습니다 (이미 클래스가 있어야 정상)."
    if total > 1:
        return None, f"'{char_id}' inline CREATE 가 {total}개라 자동 정리할 수 없습니다 (소스에서 직접 정리하세요)."
    if len(direct) != 1:
        return None, f"'{char_id}' inline CREATE 가 루프/분기 안에 있어 자동 정리할 수 없습니다."
    return direct[0], ""


def _can_create_character_source(world_id: str, char: dict) -> bool:
    """missing_character_source 이슈를 안전하게 자동 분리할 수 있는지 dry-run 판정합니다."""
    char_id = str(char.get("id") or "")
    if not char_id or se.find_character_file(world_id, char_id) is not None:
        return False
    stmt, _reason = _check_inline_create(world_id, char_id)
    return stmt is not None


def _reconstruct_default_cfg(char: dict) -> dict:
    """컴파일된 char 프로파일을 DEFAULT_CFG dict 로 충실히 재구성합니다.

    스캐폴드 _default_cfg 골격을 덧대지 않는다: 골격 키를 추가하면 재컴파일 시 blob 이
    원본과 달라져 verify-by-recompile 동일성 검증에 걸리기 때문(컴파일된 값만 그대로 사용).
    state 는 컴파일된 DynamicState 값(이미 유효 컬럼)에서 PK id 만 제거해 그대로 보존한다 —
    커스텀 컬럼도 살려야 재생성 시 insert_state 가 동일하게 빌드해 동일성 검증을 통과한다.
    """
    state = {key: value for key, value in (char.get("state") or {}).items() if key != "id"}
    return {
        "static": dict(char.get("static") or {}),
        "personality": dict(char.get("personality") or {}),
        "info": dict(char.get("info") or {}),
        "state": state,
    }


def _remove_inline_create(schema_path: Path, char_id: str) -> dict:
    """schema.py 에서 char_id inline CREATE 문장 한 건을 줄 단위로 제거합니다 (ast 검증 후 기록)."""
    text = schema_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return se._fail(f"schema.py 파싱 실패: {e}")
    direct, total = _find_inline_char_creates(tree, char_id)
    if total != 1 or len(direct) != 1:
        return se._fail(f"제거할 단일 inline CREATE 를 특정하지 못했습니다 (총 {total}건).")
    stmt = direct[0]
    # 1-based lineno → 줄 단위 슬라이스로 [lineno, end_lineno] 구간(끝줄 포함)을 통째로 제거.
    lines = text.splitlines(keepends=True)
    new_text = "".join(lines[: stmt.lineno - 1] + lines[stmt.end_lineno:])
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return se._fail(f"CREATE 제거 결과가 파싱되지 않습니다: {e}")
    try:
        se._safe_write(schema_path, new_text)
    except OSError as e:
        return se._fail(f"schema.py 기록 실패: {e}")
    return {"ok": True, "message": "inline CREATE 제거 완료."}


def _apply_character_source(world_id: str, char_id: str, char_type: str, source: str) -> dict:
    """파일 생성·등록·inline CREATE 제거를 적용하고 verify-by-recompile 후 유지/롤백합니다.

    적용 전후 모든 시나리오의 char 스냅샷이 동일해야 한다(PK 중복=재컴파일 예외, 드리프트=불일치).
    하나라도 어긋나면 캡처해 둔 원본으로 즉시 복원한다.
    """
    pkg = world_pkg_dir(world_id)
    schema_path = pkg / "schema.py"
    init_path = pkg / "characters" / "__init__.py"
    new_file = pkg / "characters" / f"{char_id}.py"

    scenario_ids = migrate._scenario_ids(world_id)
    before = migrate._char_snapshot(world_id, char_id, scenario_ids)

    # 롤백용 원본 캡처 (.bak 에 의존하지 않고 직접 복원 — 다중 파일 원자성 확보).
    orig_schema = schema_path.read_text(encoding="utf-8")
    orig_init = init_path.read_text(encoding="utf-8") if init_path.exists() else None

    def _rollback() -> None:
        """모든 쓰기를 원복하고 모듈/그래프 캐시를 무효화합니다."""
        schema_path.write_text(orig_schema, encoding="utf-8")
        if orig_init is None:
            init_path.unlink(missing_ok=True)
        else:
            init_path.write_text(orig_init, encoding="utf-8")
        new_file.unlink(missing_ok=True)
        compiler.invalidate(world_id)

    # 1. 새 캐릭터 파일 작성.
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text(source, encoding="utf-8")

    # 2. characters/__init__.py + schema.py(import·chars·narrator/pc) 등록.
    reg = source_create.register_character(world_id, scaffold._camel(char_id), char_id, char_type)
    if not reg.get("ok"):
        _rollback()
        return se._fail(f"등록 실패: {reg.get('message')}")

    # 3. schema.py inline CREATE 제거 (등록으로 오프셋이 바뀌므로 재파싱해 다시 특정).
    removed = _remove_inline_create(schema_path, char_id)
    if not removed.get("ok"):
        _rollback()
        return se._fail(f"inline CREATE 제거 실패: {removed.get('message')}")

    # 4. verify-by-recompile — PK 중복(재컴파일 예외)·그래프 드리프트 차단.
    try:
        after = migrate._char_snapshot(world_id, char_id, scenario_ids)
    except Exception as e:
        _rollback()
        return se._fail(f"재컴파일 실패 (PK 중복 등 가능): {e}")
    if before != after:
        mismatched = [sid for sid in before if before[sid] != after.get(sid)]
        _rollback()
        return se._fail(
            f"적용 전후 그래프가 달라 적용하지 않았습니다 (시나리오: {', '.join(mismatched)}). "
            "이 캐릭터는 자동 파일화가 안전하지 않습니다; 소스에서 직접 정리하세요."
        )

    compiler.invalidate(world_id)
    return {
        "ok": True,
        "message": f"'{char_id}' 를 클래스 파일로 분리하고 schema.py inline CREATE 를 제거했습니다 (그래프 동일성 검증 통과).",
        "diff": source,
        "backup": reg.get("backup"),
        "formatted": True,
    }


def _repair_missing_character_source(world_id: str, char: dict, apply: bool) -> dict:
    """클래스 없이 raw CREATE 로만 존재하는 Character 를 클래스 파일로 안전하게 분리합니다.

    파일 생성 + chars 등록 + schema.py inline CREATE 제거를 한 묶음으로 적용하고,
    verify-by-recompile 로 그래프 동일성(=PK 중복·드리프트 없음)을 보장합니다.
    모호하면 정밀 사유로 거부합니다(현재처럼 막연히 막지 않음).
    """
    char_id = str(char.get("id") or "")
    if not char_id:
        return se._fail("캐릭터 id가 없어 자동 생성할 수 없습니다.")
    if se.find_character_file(world_id, char_id) is not None:
        return {"ok": True, "message": "이미 캐릭터 파일이 있습니다.", "diff": "", "backup": None, "formatted": False}

    # 안전성 판정: 단일 함수-직속 inline CREATE 만 자동 정리 대상.
    create_stmt, reason = _check_inline_create(world_id, char_id)
    if create_stmt is None:
        return se._fail(reason)

    char_type = str(char.get("type") or "npc")
    source = scaffold.character_source_from_cfg(
        char_id,
        str(char.get("name") or char_id),
        list(char.get("aliases") or []),
        char_type,
        _reconstruct_default_cfg(char),
    ).replace("%%WID%%", world_id)

    if not apply:
        return {
            "ok": True,
            "message": f"새 캐릭터 파일 '{char_id}.py' 생성 + schema.py inline CREATE 제거 1건 (적용 시 재컴파일로 동일성 검증).",
            "diff": source,
            "backup": None,
            "formatted": True,
        }

    return _apply_character_source(world_id, char_id, char_type, source)
