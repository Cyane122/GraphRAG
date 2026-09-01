# ================================
# src/apps/world_editor/source_ops/locators.py
#
# Low-level AST locators, literal checks, source-write helpers, and rewrite constants.
#
# Functions
#   - merge_cfg_dict(base: dict, override: dict) -> dict : Recursively merge configuration dictionaries.
# ================================

from __future__ import annotations

import ast
import os
import shutil
import sys
from pathlib import Path

from src.apps.world_editor.source_ops.text import (
    _base_indent,
    _byte_col_to_codepoint,
    _emit,
    _line_offsets,
    _literal_eval_segment,
    _node_span,
    _replace_node_span,
)
from src.apps.world_editor.worlds import world_pkg_dir

_ROLE_LABEL: dict[str, str] = {
    "static": "StaticProfile",
    "personality": "Personality",
    "info": "DynamicInformation",
}


_TUPLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "location": ("id", "name", "description", "prompt_hint", "prompt_priority", "tags", "links", "scenarios"),
    "rule": ("rule_id", "name", "summary", "prompt_hint", "prompt_priority", "tags", "location_id", "scenarios"),
}


_SCHEDULE_EDITABLE_FIELDS: tuple[str, ...] = (
    "name",
    "activity",
    "summary",
    "prompt_hint",
    "prompt_priority",
    "recurrence",
    "day_of_week",
    "day_of_weeks",
    "date",
    "start_time",
    "end_time",
    "location_id",
    "status",
    "tags",
)


def _safe_write(path: Path, new_text: str) -> str:
    """원본을 .bak 으로 백업한 뒤 .tmp 경유 atomic write 로 교체합니다.

    1) 백업 — 매번 덮어쓴다(직전 1세대 보존). 2) tmp 작성 후 os.replace 로
    원자적 교체. 부분 기록으로 인한 파일 손상을 방지합니다.
    반환값은 백업 파일 경로 문자열.
    """
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)  # 메타데이터 포함 복사
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(new_text.encode("utf-8"))
    os.replace(tmp, path)  # 원자적 rename — 같은 볼륨이라 atomic 보장
    return str(bak)


def _ok(message: str, backup: str) -> dict:
    """성공 결과 dict 를 만듭니다."""
    return {"ok": True, "message": message, "backup": backup, "formatted": True}


def _fail(message: str) -> dict:
    """실패 결과 dict 를 만듭니다 (파일 무변경)."""
    return {"ok": False, "message": message, "backup": None, "formatted": False}


def _assign_target_names(stmt: ast.stmt) -> tuple[list[str], ast.expr | None]:
    """Assign/AnnAssign 문에서 (대상 이름 리스트, 값 노드)를 통일된 형태로 추출합니다.

    소스에는 `_RELS = {...}` (Assign)뿐 아니라
    `_RELS: dict[...] = {...}` (AnnAssign)도 등장하므로 둘 다 처리합니다.
    AnnAssign 은 단일 타깃(`Name`)이고 값이 없을 수도 있습니다(`x: int`).
    """
    if isinstance(stmt, ast.Assign):
        names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        return names, stmt.value
    if isinstance(stmt, ast.AnnAssign):
        # AnnAssign.target 는 단일 노드. 값이 없으면(None) 호출부가 걸러낸다.
        if isinstance(stmt.target, ast.Name):
            return [stmt.target.id], stmt.value
    return [], None


def _iter_classes(tree: ast.Module) -> list[ast.ClassDef]:
    """모듈 최상위 + 중첩 없이 모든 ClassDef 를 수집합니다.

    캐릭터 파일은 한 파일에 여러 클래스(예: han_yuram_family.py)가 있을 수
    있으므로 walk 로 전부 훑습니다.
    """
    return [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]


def _class_id_value(cls: ast.ClassDef) -> str | None:
    """클래스 body 의 `id = "..."` 할당에서 문자열 리터럴 값을 추출합니다.

    park_sian: `id = "park_sian"`, kim_nayun: `id = 'kim_nayun'` 처럼
    따옴표 종류가 섞여도 literal_eval 로 일관되게 처리합니다.
    """
    for stmt in cls.body:
        # `id = "..."` (Assign) 또는 `id: str = "..."` (AnnAssign) 모두 인정.
        names, value = _assign_target_names(stmt)
        if "id" in names and value is not None:
            try:
                val = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
            return val if isinstance(val, str) else None
    return None


def _find_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef | None:
    """클래스 body 에서 지정 이름의 메서드(FunctionDef)를 찾습니다."""
    for stmt in cls.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == name:
            return stmt
    return None


def _find_character_class(tree: ast.Module, char_id: str) -> ast.ClassDef | None:
    """char_id 와 일치하는 `id` 를 가진 ClassDef 를 반환합니다."""
    for cls in _iter_classes(tree):
        if _class_id_value(cls) == char_id:
            return cls
    return None


def _class_attr_node(cls: ast.ClassDef, attr: str) -> ast.expr | None:
    """클래스 body 직속 attr 할당값 노드를 반환합니다."""
    for stmt in cls.body:
        names, value = _assign_target_names(stmt)
        if attr in names:
            return value
    return None


def _class_attr_dict(cls: ast.ClassDef, attr: str) -> tuple[dict, bool, str]:
    """클래스 attr dict 리터럴을 (값, 편집가능여부, 사유)로 반환합니다.

    attr 이 없으면 Character 베이스의 빈 dict 를 상속하는 것으로 보고, 새 리터럴 생성이
    가능하므로 editable=True 로 취급합니다.
    """
    node = _class_attr_node(cls, attr)
    if node is None:
        return {}, True, ""
    if not isinstance(node, ast.Dict) or not _is_clean_literal_node(node):
        return {}, False, f"{attr} is not a clean literal dict"
    value = ast.literal_eval(node)
    if not isinstance(value, dict):
        return {}, False, f"{attr} is not a dict"
    return value, True, ""


def merge_cfg_dict(base: dict, override: dict) -> dict:
    """Character.DEFAULT_CFG와 SCENARIO_OVERRIDES처럼 dict를 재귀 병합합니다."""
    merged = dict(base)
    for key, value in override.items():
        old_value = merged.get(key)
        if isinstance(old_value, dict) and isinstance(value, dict):
            merged[key] = merge_cfg_dict(old_value, value)
        else:
            merged[key] = value
    return merged


def _character_cfg_meta(cls: ast.ClassDef, scenario_id: str | None) -> dict:
    """캐릭터 클래스의 DEFAULT_CFG / SCENARIO_OVERRIDES 병합 메타를 반환합니다."""
    sid = scenario_id or "default"
    default_cfg, default_editable, default_reason = _class_attr_dict(cls, "DEFAULT_CFG")
    overrides, override_editable, override_reason = _class_attr_dict(cls, "SCENARIO_OVERRIDES")
    scenario_override = {}
    if override_editable:
        raw_override = overrides.get(sid, {})
        if isinstance(raw_override, dict):
            scenario_override = raw_override
        else:
            override_editable = False
            override_reason = f"SCENARIO_OVERRIDES[{sid!r}] is not a dict"
    effective = merge_cfg_dict(default_cfg, scenario_override)
    return {
        "scenario_id": sid,
        "default": default_cfg,
        "override": scenario_override,
        "all_overrides": overrides if override_editable else {},
        "effective": effective,
        "editable": {
            "default": {"editable": default_editable, "reason": default_reason},
            "override": {"editable": override_editable, "reason": override_reason},
        },
    }


def _is_clean_literal_node(node: ast.AST) -> bool:
    """노드가 ast.literal_eval 로 평가 가능한 정적 리터럴인지 검사합니다.

    f-string, 이름 참조, 함수 호출, ** 스플랫 등은 모두 False.
    literal_eval 은 str-concat(ast.Add)도 거부하므로, 멀티라인 괄호 묶음
    문자열( "a" "b" 암시적 연결, ast.Constant 로 폴딩됨)만 통과한다.
    """
    try:
        ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return False
    return True


def _find_rel_dicts(method: ast.FunctionDef) -> list[ast.Dict]:
    """build_relationship body 의 '값이 dict 리터럴'인 할당들을 수집합니다.

    park_sian 의 _VOLLEYBALL_RELS, _RELS 처럼 other_id→4-tuple 매핑 dict 만
    대상. combined = dict(_RELS) 같은 호출은 dict 리터럴이 아니라 제외된다.
    """
    dicts: list[ast.Dict] = []
    for stmt in method.body:
        # 주석형(`_RELS: dict[...] = {...}`) 할당도 잡아야 한다.
        _names, value = _assign_target_names(stmt)
        if isinstance(value, ast.Dict):
            dicts.append(value)
    return dicts


def _rel_value_node_for(dicts: list[ast.Dict], target: str) -> tuple[ast.AST | None, str]:
    """관계 dict 들 중 target 키를 가진 '유일한' 항목의 값 노드를 찾습니다.

    반환: (값 노드 | None, 사유). 편집 가능 조건(스펙):
      - target 을 키로 갖는 dict 리터럴이 정확히 1개
      - 그 값이 len==4 인 리터럴 tuple/list
    0개 → not editable, 2개 이상 → 모호하므로 거부.
    """
    matches: list[ast.AST] = []
    for d in dicts:
        for key_node, val_node in zip(d.keys, d.values):
            # 키가 문자열 상수이고 target 과 일치하는지.
            if isinstance(key_node, ast.Constant) and key_node.value == target:
                matches.append(val_node)

    if len(matches) == 0:
        return None, "target not in a literal relationship dict"
    if len(matches) > 1:
        return None, "target appears in multiple relationship dicts"

    val = matches[0]
    # 값이 리터럴 4-튜플/리스트인지 확인.
    if not isinstance(val, (ast.Tuple, ast.List)):
        return None, "relationship value is not a literal tuple"
    if not _is_clean_literal_node(val):
        return None, "relationship value is not a clean literal"
    evaluated = ast.literal_eval(val)
    if len(evaluated) != 4:
        return None, "relationship tuple is not length 4"
    return val, ""


def _find_blob_call(method: ast.FunctionDef, label: str) -> tuple[ast.Call | None, str]:
    """build_schema 내 insert_static_inline 호출 중 4번째 위치 인자가 label 인 것을 찾습니다.

    반환: (Call 노드 | None, 사유). 편집 가능 조건(스펙):
      - 해당 Call 이 *args/**kwargs(splat) 없음
      - 모든 키워드 값이 정적 리터럴
    park_sian: HAS_INFO 블록은 **info_props 라서 거부, static 블록은 전부 리터럴이라 허용.
    """
    target_call: ast.Call | None = None
    for call in ast.walk(method):
        if not isinstance(call, ast.Call):
            continue
        # 함수 이름이 insert_static_inline 인지.
        if not (isinstance(call.func, ast.Name) and call.func.id == "insert_static_inline"):
            continue
        # 4번째 위치 인자(index 3)가 label 문자열 상수인지.
        if len(call.args) < 4:
            continue
        arg4 = call.args[3]
        if isinstance(arg4, ast.Constant) and arg4.value == label:
            target_call = call
            break

    if target_call is None:
        return None, f"no insert_static_inline call for label {label}"

    # **kwargs splat 검사 — keyword.arg 가 None 이면 ** 스플랫이다.
    for kw in target_call.keywords:
        if kw.arg is None:
            return None, "uses computed/spread values; edit in source"
    # *args splat 검사.
    for a in target_call.args:
        if isinstance(a, ast.Starred):
            return None, "uses computed/spread values; edit in source"
    # 모든 키워드 값이 리터럴인지.
    for kw in target_call.keywords:
        if not _is_clean_literal_node(kw.value):
            return None, "uses computed/spread values; edit in source"

    return target_call, ""


def _is_scenario_ref(node: ast.AST) -> bool:
    """AST 노드가 self.scenario_id 참조인지 반환합니다."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "scenario_id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _scenario_test_matches(test: ast.AST, scenario_id: str) -> bool | None:
    """정적 self.scenario_id 조건식이 scenario_id와 매칭되는지 반환합니다."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        left = test.left
        right = test.comparators[0]
        op = test.ops[0]
        if isinstance(op, ast.Eq):
            if _is_scenario_ref(left) and isinstance(right, ast.Constant):
                return right.value == scenario_id
            if _is_scenario_ref(right) and isinstance(left, ast.Constant):
                return left.value == scenario_id
        if isinstance(op, ast.In) and _is_scenario_ref(left) and _is_clean_literal_node(right):
            values = ast.literal_eval(right)
            return scenario_id in values
    return None


def _direct_state_dict(body: list[ast.stmt]) -> tuple[ast.Dict | None, str]:
    """문장 목록 직속의 `_state = {literal}` 할당을 찾습니다."""
    direct: list[ast.Dict] = []
    for stmt in body:
        names, value = _assign_target_names(stmt)
        if "_state" in names and isinstance(value, ast.Dict):
            direct.append(value)
    if len(direct) == 0:
        return None, "state dict not found in selected block"
    if len(direct) > 1:
        return None, "multiple _state assignments in selected block"
    node = direct[0]
    if not _is_clean_literal_node(node):
        return None, "state is not a clean literal dict"
    return node, ""


def _find_conditional_state_dict(method: ast.FunctionDef, scenario_id: str) -> tuple[ast.Dict | None, str]:
    """정적 if/elif/else self.scenario_id 분기에서 현재 시나리오의 _state dict를 찾습니다."""
    for stmt in method.body:
        if not isinstance(stmt, ast.If):
            continue
        current: ast.If | None = stmt
        fallback: list[ast.stmt] | None = None
        while current is not None:
            match = _scenario_test_matches(current.test, scenario_id)
            if match is True:
                return _direct_state_dict(current.body)
            if match is None:
                break
            if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
                current = current.orelse[0]
                continue
            fallback = current.orelse
            current = None
        if fallback:
            return _direct_state_dict(fallback)
    return None, "scenario-conditional state; edit in source"


def _find_state_dict(method: ast.FunctionDef, scenario_id: str | None = None) -> tuple[ast.Dict | None, str]:
    """build_schema 안의 현재 시나리오에 대응하는 `_state = {literal}` 할당을 찾습니다.

    반환: (dict 노드 | None, 사유). 편집 가능 조건(스펙):
      - `_state = {...}` 가 함수 body 바로 아래(If/For/While/With/Try 밖)에 정확히 1개
      - 값이 리터럴 dict
      - 또는 self.scenario_id 정적 if/elif/else 분기 안의 현재 scenario_id branch에 정확히 1개
    """
    node, reason = _direct_state_dict(method.body)
    if node is not None:
        return node, ""
    if scenario_id:
        return _find_conditional_state_dict(method, scenario_id)
    return None, "scenario-conditional state; edit in source"


def _find_tuple_row(tree: ast.Module, kind: str, row_id: str) -> tuple[ast.Tuple | None, str]:
    """schema.py 의 '리스트-리터럴 = [tuple, ...]' 할당에서 첫 컬럼==row_id 인 행을 찾습니다.

    반환: (튜플 노드 | None, 사유). 편집 가능 조건(스펙):
      - 모듈 어딘가의 list 리터럴 할당값 안에 첫 원소가 row_id 인 리터럴 튜플이 존재
      - 그 튜플 arity == 템플릿 arity(8)
    sunghwa: locations 는 build_schema 내부 inline list(모듈 할당 아님)이고 arity 6 →
    여기서 arity 불일치로 거부. _RULES 리스트가 없고 insert_rule 직접 호출이라 rule 도 거부.
    """
    template_arity = len(_TUPLE_COLUMNS[kind])

    # 모듈 전역 + 함수 내부 어디든 list 리터럴 할당을 모두 훑는다.
    # (단, 값이 list 리터럴인 Assign 만 — 동적 생성 리스트는 자연히 제외됨)
    candidate_rows: list[ast.Tuple] = []
    arity_mismatch = False
    for node in ast.walk(tree):
        # `_X = [...]` 와 `_X: list[tuple] = [...]` 모두 검사 (모듈 전역 주석형 포함).
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        _names, value = _assign_target_names(node)
        if not isinstance(value, ast.List):
            continue
        for elt in value.elts:
            if not isinstance(elt, ast.Tuple) or not elt.elts:
                continue
            first = elt.elts[0]
            # 첫 원소가 row_id 문자열 상수인 행만 후보.
            if isinstance(first, ast.Constant) and first.value == row_id:
                if not _is_clean_literal_node(elt):
                    continue
                if len(elt.elts) != template_arity:
                    arity_mismatch = True  # 모양은 맞는데 arity 가 다른 케이스 기록
                    continue
                candidate_rows.append(elt)

    if len(candidate_rows) == 1:
        return candidate_rows[0], ""
    if arity_mismatch:
        return None, "non-template shape; edit in source"
    return None, "non-template shape; edit in source"


def _is_self_id(node: ast.AST | None) -> bool:
    """AST 노드가 `self.id` 참조인지 반환합니다."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _eval_schedule_id_expr(node: ast.AST | None, char_id: str) -> str | None:
    """schedule_id/owner_id 표현식 중 안전하게 해석 가능한 값을 반환합니다.

    지원 범위는 문자열 상수와 `f"{self.id}_suffix"` 입니다. 다른 이름 참조나 호출은
    한 호출이 여러 캐릭터에 적용될 수 있으므로 편집 대상에서 제외합니다.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _is_self_id(node):
        return char_id
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue) and _is_self_id(value.value):
                parts.append(char_id)
                continue
            return None
        return "".join(parts)
    return None


def _call_kw_map(call: ast.Call) -> dict[str, ast.keyword]:
    """Call 키워드를 이름→keyword 노드로 반환합니다. **kwargs 는 제외합니다."""
    return {kw.arg: kw for kw in call.keywords if kw.arg is not None}


def _schedule_source_key(call: ast.Call, field: str) -> str | None:
    """UI 필드를 실제 insert_schedule kwarg 이름으로 매핑합니다."""
    kw_map = _call_kw_map(call)
    if field == "day_of_weeks":
        if "day_of_weeks" in kw_map:
            return "day_of_weeks"
        if "day_of_week" in kw_map:
            return "day_of_week"
        return None
    if field == "day_of_week":
        if "day_of_week" in kw_map:
            return "day_of_week"
        if "day_of_weeks" in kw_map:
            return "day_of_weeks"
        return None
    return field if field in kw_map else None


def _find_schedule_call(method: ast.FunctionDef, char_id: str, schedule_id: str) -> tuple[ast.Call | None, str]:
    """build_schema 안에서 char_id/schedule_id 에 대응하는 insert_schedule 호출을 찾습니다.

    편집 범위는 캐릭터 파일의 `owner_id=self.id` 형태 호출입니다. 월드 schema 반복문처럼
    `owner_id=char_id` 로 여러 캐릭터를 생성하는 호출은 한 캐릭터 편집으로 전체 호출이
    바뀔 수 있으므로 대상에서 제외합니다.
    """
    matches: list[ast.Call] = []
    saw_schedule = False
    for call in ast.walk(method):
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Name) and call.func.id == "insert_schedule"):
            continue
        saw_schedule = True
        if any(kw.arg is None for kw in call.keywords):
            continue
        kw_map = _call_kw_map(call)
        owner_node = kw_map["owner_id"].value if "owner_id" in kw_map else (call.args[1] if len(call.args) > 1 else None)
        if not _is_self_id(owner_node):
            continue
        schedule_node = kw_map["schedule_id"].value if "schedule_id" in kw_map else (call.args[2] if len(call.args) > 2 else None)
        if _eval_schedule_id_expr(schedule_node, char_id) == schedule_id:
            matches.append(call)

    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return None, "schedule_id appears in multiple insert_schedule calls"
    if saw_schedule:
        return None, "matching insert_schedule call is computed or shared; edit in source"
    return None, "insert_schedule call not found"


def _schedule_edit_meta(call: ast.Call) -> dict:
    """insert_schedule 호출에서 UI가 편집 가능한 필드와 잠긴 필드 사유를 계산합니다."""
    kw_map = _call_kw_map(call)
    fields: dict[str, dict] = {}
    locked: dict[str, str] = {
        "schedule_id": "f-string/식별자는 원본을 보존합니다.",
    }
    if "material" in kw_map:
        locked["material"] = "material 은 json.dumps 등 계산식일 수 있어 소스에서 편집하세요."

    for field in _SCHEDULE_EDITABLE_FIELDS:
        source_key = _schedule_source_key(call, field)
        if source_key is None:
            continue
        value_node = kw_map[source_key].value
        if _is_clean_literal_node(value_node):
            fields[field] = {"source_key": source_key}
        else:
            locked[field] = "computed value; edit in source"
    return {"fields": fields, "locked": locked}


def _coerce_weekday_set(value: object) -> set[int]:
    """UI 입력값을 day_of_week/day_of_weeks set[int] 리터럴로 정규화합니다."""
    if isinstance(value, int):
        return {value}
    if isinstance(value, str):
        raw_values = [v.strip() for v in value.split(",") if v.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raise ValueError("day_of_weeks 는 숫자 또는 숫자 리스트여야 합니다.")

    result: set[int] = set()
    for raw in raw_values:
        day = int(raw)
        if day < 0 or day > 6:
            raise ValueError("요일은 0~6 범위여야 합니다.")
        result.add(day)
    return result


def _quote_string_like(old_src: str, value: str) -> str:
    """기존 문자열 리터럴의 따옴표 스타일을 따라 새 문자열 리터럴을 만듭니다."""
    stripped = old_src.lstrip()
    if not stripped.startswith('"'):
        return repr(value)
    escaped = (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _emit_like_old(old_src: str, value: object, base_indent: str) -> str:
    """기존 소스 조각의 스타일을 가능한 한 보존해 새 리터럴을 렌더링합니다."""
    if isinstance(value, str):
        return _quote_string_like(old_src, value)
    return _emit(value, base_indent)






__all__ = [name for name in globals() if not name.startswith("__")]
