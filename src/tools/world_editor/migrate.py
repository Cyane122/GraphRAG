# ================================
# src/tools/world_editor/migrate.py
#
# 손글씨 캐릭터(insert_static_inline + scenario 분기 imperative build_schema)를
# cfg 패턴(DEFAULT_CFG + SCENARIO_OVERRIDES + super().build_schema)으로 변환합니다.
# 변환은 보수적입니다: 정적 리터럴로 추출 가능한 부분만 cfg 로 옮기고, 그 외(schedule
# 등)는 build_schema 잔여 본문으로 보존하며, 모호하면 변환을 거부합니다.
# 안전 핵심: 변환 후 모든 시나리오를 임시 DB로 재컴파일해 노드가 변환 전과 완전히
# 동일할 때만 적용합니다(verify-by-recompile). 불일치 시 .bak 으로 즉시 복원합니다.
#
# Functions
#   - analyze_character(world_id: str, char_id: str) -> dict : 변환 가능성 + 추출 결과 미리보기.
#   - migrate_character(world_id: str, char_id: str, apply: bool = False) -> dict : diff/apply.
# ================================

from __future__ import annotations

import ast
import difflib

from src.assets.worlds.base import _DYNAMIC_STATE_COLUMNS
from src.tools.world_editor import compiler
from src.tools.world_editor import source_edit as se
from src.tools.world_editor.worlds import scenario_infos

# insert_static_inline label → cfg 섹션 키. base_character.Character._PROFILE_NODES 와 정합.
_LABEL_SECTION: dict[str, str] = {
    "StaticProfile": "static",
    "Personality": "personality",
    "DynamicInformation": "info",
}


# ──────────────────────────────────────────────────────────────────────
# 결과 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _fail(message: str, **extra) -> dict:
    """변환 불가/실패 결과 (파일 무변경)."""
    return {"ok": False, "migratable": False, "message": message, **extra}


# ──────────────────────────────────────────────────────────────────────
# AST 소형 헬퍼
# ──────────────────────────────────────────────────────────────────────

def _call_of(stmt: ast.stmt) -> ast.Call | None:
    """Expr(Call) 문이면 그 Call 을, 아니면 None 을 반환합니다."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return stmt.value
    return None


def _is_bare_string(stmt: ast.stmt) -> bool:
    """문장이 docstring 같은 bare 문자열 상수(no-op)인지 판정합니다."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_character_create_stmt(stmt: ast.stmt) -> bool:
    """Character 노드를 생성하는 top-level 문장인지 판정합니다.

    인라인 conn.execute("CREATE (:Character ...)") 또는 _create_character_node 처럼
    'character node 생성' 헬퍼 호출을 인정합니다. 헬퍼가 그 외 일을 더 하면
    verify-by-recompile 이 그래프 불일치로 잡아 변환을 거부하므로 안전합니다.
    """
    call = _call_of(stmt)
    if call is None:
        return False
    if "CREATE (:Character" in _execute_sql(call):
        return True
    func = call.func
    if isinstance(func, ast.Name):
        name = func.id.lower()
        return "character" in name and ("create" in name or "node" in name)
    return False


def _execute_sql(call: ast.Call) -> str:
    """conn.execute('...') 호출의 첫 인자 문자열 리터럴을 반환합니다 (아니면 '')."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
        return ""
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return ""


def _is_insert_static_inline(call: ast.Call) -> bool:
    """insert_static_inline(...) 호출인지 판정합니다."""
    return isinstance(call.func, ast.Name) and call.func.id == "insert_static_inline"


def _eval_literal_dict(node: ast.AST, drop_keys: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """ast.Dict 리터럴을 {str: 값} 으로 평가합니다.

    drop_keys 에 든 키는 값이 비리터럴이어도 무시하고 건너뜁니다(예: 'id' = f"{self.id}_state").
    그 외 키의 값이 비리터럴이면 (None, 사유) 를 반환합니다.
    """
    if not isinstance(node, ast.Dict):
        return None, "값이 dict 리터럴이 아닙니다"
    result: dict = {}
    for key_node, val_node in zip(node.keys, node.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            return None, "dict 키가 문자열 상수가 아닙니다"
        key = key_node.value
        if key in drop_keys:
            continue
        if not se._is_clean_literal_node(val_node):
            return None, f"'{key}' 값이 계산식입니다; 소스에서 편집하세요"
        result[key] = ast.literal_eval(val_node)
    return result, ""


def _eval_state_dict(node: ast.AST) -> tuple[dict | None, str]:
    """DynamicState 파라미터 dict 리터럴을 cfg['state'] 용으로 평가합니다.

    계산식 값은: id 키이거나 DynamicState 컬럼이 아니면(예: placeholder 'state_id')
    무시합니다(base 가 id 를 재생성하고, 컬럼이 아닌 키는 build 시 어차피 필터됨).
    그 외(실제 컬럼에 계산식 값)는 (None, 사유)로 거부합니다.
    """
    if not isinstance(node, ast.Dict):
        return None, "값이 dict 리터럴이 아닙니다"
    result: dict = {}
    for key_node, val_node in zip(node.keys, node.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            return None, "dict 키가 문자열 상수가 아닙니다"
        key = key_node.value
        if se._is_clean_literal_node(val_node):
            result[key] = ast.literal_eval(val_node)
            continue
        if key == "id" or key not in _DYNAMIC_STATE_COLUMNS:
            continue  # 노드 id(재생성) 또는 비-컬럼 placeholder → 무시.
        return None, f"'{key}' 값이 계산식입니다; 소스에서 편집하세요"
    return result, ""


def _flatten_if_chain(if_stmt: ast.If) -> list[tuple[ast.expr | None, list[ast.stmt]]]:
    """if/elif/else 체인을 [(test|None, body), ...] 로 평탄화합니다 (else 는 test=None)."""
    chain: list[tuple[ast.expr | None, list[ast.stmt]]] = []
    current: ast.If | None = if_stmt
    while current is not None:
        chain.append((current.test, current.body))
        if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
            current = current.orelse[0]
            continue
        if current.orelse:
            chain.append((None, current.orelse))
        current = None
    return chain


def _is_scenario_ref_test(test: ast.expr) -> bool:
    """test 가 구조적으로 self.scenario_id 비교(==/in)인지 (sid 값과 무관하게) 판정합니다."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        left, right, op = test.left, test.comparators[0], test.ops[0]
        if isinstance(op, ast.Eq):
            return (
                (se._is_scenario_ref(left) and isinstance(right, ast.Constant))
                or (se._is_scenario_ref(right) and isinstance(left, ast.Constant))
            )
        if isinstance(op, ast.In):
            return se._is_scenario_ref(left) and se._is_clean_literal_node(right)
    return False


def _match_scenario(test: ast.expr, scenario_ids: list[str]) -> tuple[str | None, str]:
    """scenario 분기 조건을 등록된 시나리오에 매칭합니다.

    반환 (sid, reason): reason 이 "" 면 sid 와 정상 매칭. 그 외에는 변환을 거부하고
    그 사유를 그대로 사용자에게 전달합니다(문제를 숨기지 않고 드러낸다).
    """
    for sid in scenario_ids:
        if se._scenario_test_matches(test, sid) is True:
            return sid, ""
    if _is_scenario_ref_test(test):
        # scenario_id 분기지만 등록된 시나리오와 매칭 안 됨 → 오타/누락 가능성. (자동 처리 안 함)
        return None, "조건이 등록되지 않은 scenario_id 를 가리킵니다 (schema.py SCENARIOS 확인)"
    return None, "조건이 정적 scenario_id 분기가 아닙니다; 소스에서 편집하세요"


# ──────────────────────────────────────────────────────────────────────
# cfg 추출
# ──────────────────────────────────────────────────────────────────────

class _Extracted:
    """build_schema 에서 추출한 cfg + 잔여 문장 모음."""

    def __init__(self) -> None:
        self.default_cfg: dict = {}
        self.overrides: dict[str, dict] = {}
        self.consumed: set[int] = set()        # id(stmt) — cfg 로 흡수한 top-level 문장
        self.assigned_names: set[str] = set()   # 소비한 로컬 변수명 (_state, info_props 등)

    def add_override(self, sid: str, section: str, value: dict) -> None:
        self.overrides.setdefault(sid, {})[section] = value


def _extract_blob(method: ast.FunctionDef, ex: _Extracted, label: str, section: str,
                  scenario_ids: list[str]) -> str:
    """label(StaticProfile/Personality/DynamicInformation) 의 insert_static_inline 을 cfg 로 추출합니다.

    지원: (a) 리터럴 kwargs, (b) 단일 **Name splat + base dict 리터럴 + scenario .update({리터럴}).
    노드가 없으면 '' 반환(섹션 없음). 추출 불가면 사유 문자열 반환.
    """
    # label 에 해당하는 top-level insert_static_inline Expr 문장 찾기.
    target_stmt: ast.stmt | None = None
    target_call: ast.Call | None = None
    for stmt in method.body:
        call = _call_of(stmt)
        if call and _is_insert_static_inline(call) and len(call.args) >= 4:
            arg4 = call.args[3]
            if isinstance(arg4, ast.Constant) and arg4.value == label:
                target_stmt, target_call = stmt, call
                break
    if target_call is None:
        return ""  # 이 라벨의 노드가 없는 캐릭터 — 정상.

    splats = [kw for kw in target_call.keywords if kw.arg is None]
    literal_kw = [kw for kw in target_call.keywords if kw.arg is not None]

    # (a) 리터럴 kwargs 패턴.
    if not splats:
        props: dict = {}
        for kw in literal_kw:
            if not se._is_clean_literal_node(kw.value):
                return f"{label}: '{kw.arg}' 값이 계산식입니다; 소스에서 편집하세요"
            props[kw.arg] = ast.literal_eval(kw.value)
        ex.default_cfg[section] = props
        ex.consumed.add(id(target_stmt))
        return ""

    # (b) 단일 **Name splat 패턴.
    if len(splats) != 1 or literal_kw:
        return f"{label}: 복합 spread 인자; 소스에서 편집하세요"
    splat_val = splats[0].value
    if not isinstance(splat_val, ast.Name):
        return f"{label}: spread 대상이 변수명이 아닙니다; 소스에서 편집하세요"
    var = splat_val.id

    # base 할당 `var = {리터럴}` 찾기.
    base_stmt: ast.stmt | None = None
    base_dict: dict | None = None
    for stmt in method.body:
        names, value = se._assign_target_names(stmt)
        if var in names:
            if base_stmt is not None:
                return f"{label}: '{var}' 가 여러 번 할당됩니다; 소스에서 편집하세요"
            evaluated, reason = _eval_literal_dict(value, drop_keys=())
            if evaluated is None:
                return f"{label}: {reason}"
            base_stmt, base_dict = stmt, evaluated
    if base_stmt is None or base_dict is None:
        return f"{label}: '{var}' base dict 를 찾지 못했습니다; 소스에서 편집하세요"

    ex.default_cfg[section] = base_dict
    ex.consumed.add(id(target_stmt))
    ex.consumed.add(id(base_stmt))
    ex.assigned_names.add(var)

    # scenario 조건부 `if ...: var.update({리터럴})` 블록 흡수.
    for stmt in method.body:
        if not isinstance(stmt, ast.If):
            continue
        if not _references_update_of(stmt, var):
            continue
        # 이 if 가 오직 var.update(...) 만 담고 있는지 확인 (mixed 면 거부).
        sid, reason = _match_scenario(stmt.test, scenario_ids)
        if reason:
            return f"{label}: '{var}.update' {reason}"
        if stmt.orelse or len(stmt.body) != 1:
            return f"{label}: '{var}.update' 블록이 단순하지 않습니다; 소스에서 편집하세요"
        upd_call = _call_of(stmt.body[0])
        if not _is_update_call(upd_call, var):
            return f"{label}: '{var}.update' 블록이 단순하지 않습니다; 소스에서 편집하세요"
        delta, reason = _eval_literal_dict(upd_call.args[0], drop_keys=())
        if delta is None:
            return f"{label}: scenario delta {reason}"
        ex.add_override(sid, section, delta)
        ex.consumed.add(id(stmt))
    return ""


def _references_update_of(stmt: ast.AST, var: str) -> bool:
    """stmt 안에 var.update(...) 호출이 있는지 검사합니다."""
    for node in ast.walk(stmt):
        if _is_update_call(node, var):
            return True
    return False


def _is_update_call(node: ast.AST | None, var: str) -> bool:
    """node 가 var.update({...}) 호출인지 판정합니다."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == var
        and len(node.args) == 1
        and not node.keywords
    )


def _extract_state(method: ast.FunctionDef, ex: _Extracted, scenario_ids: list[str]) -> str:
    """DynamicState(_state 리터럴 + CREATE + HAS_STATE)를 cfg['state'] 로 추출합니다."""
    # DynamicState CREATE / HAS_STATE MATCH 문장 소비. CREATE 의 2번째 인자가
    # 인라인 dict 리터럴이면(변수 _state 없이 직접 전달하는 캐릭터) 그것을 상태원으로 보관.
    has_dynamic_state = False
    inline_state: ast.Dict | None = None
    for stmt in method.body:
        call = _call_of(stmt)
        if not call:
            continue
        sql = _execute_sql(call)
        if "CREATE (:DynamicState" in sql:
            ex.consumed.add(id(stmt))
            has_dynamic_state = True
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Dict):
                inline_state = call.args[1]
        elif "HAS_STATE" in sql:
            ex.consumed.add(id(stmt))

    # (a) 직속 `_state = {리터럴}` (무조건).
    for stmt in method.body:
        names, value = se._assign_target_names(stmt)
        if "_state" in names and isinstance(value, ast.Dict):
            evaluated, reason = _eval_state_dict(value)
            if evaluated is None:
                return f"state: {reason}"
            ex.default_cfg["state"] = evaluated
            ex.consumed.add(id(stmt))
            ex.assigned_names.add("_state")
            return ""

    # (b) scenario if/elif/else 분기.
    for stmt in method.body:
        if not isinstance(stmt, ast.If):
            continue
        if not _branch_assigns_state(stmt):
            continue
        chain = _flatten_if_chain(stmt)
        for test, body in chain:
            state_dict = _branch_state_dict(body)
            if state_dict is None:
                return "state: 분기 본문에 단일 _state 리터럴이 없습니다; 소스에서 편집하세요"
            evaluated, reason = _eval_state_dict(state_dict)
            if evaluated is None:
                return f"state: {reason}"
            if test is None:
                ex.default_cfg["state"] = evaluated  # else → 기본값
            else:
                sid, reason = _match_scenario(test, scenario_ids)
                if reason:
                    return f"state: {reason}"
                ex.add_override(sid, "state", evaluated)
        ex.consumed.add(id(stmt))
        ex.assigned_names.add("_state")
        return ""

    # (c) conn.execute 에 직접 전달된 인라인 dict 리터럴.
    if inline_state is not None:
        evaluated, reason = _eval_state_dict(inline_state)
        if evaluated is None:
            return f"state: {reason}"
        ex.default_cfg["state"] = evaluated
        return ""

    if has_dynamic_state:
        return "state: DynamicState 는 있으나 상태 dict 리터럴을 추출하지 못했습니다; 소스에서 편집하세요"
    return ""  # state 없음 — 정상.


def _branch_assigns_state(if_stmt: ast.If) -> bool:
    """if 체인의 어느 분기든 _state 를 할당하면 True."""
    for _test, body in _flatten_if_chain(if_stmt):
        if _branch_state_dict(body) is not None:
            return True
    return False


def _branch_state_dict(body: list[ast.stmt]) -> ast.Dict | None:
    """분기 본문에서 단일 `_state = {literal}` 의 dict 노드를 반환합니다 (아니면 None)."""
    found: ast.Dict | None = None
    for stmt in body:
        names, value = se._assign_target_names(stmt)
        if "_state" in names and isinstance(value, ast.Dict):
            if found is not None:
                return None
            found = value
    return found


# ──────────────────────────────────────────────────────────────────────
# 분석 + 변환
# ──────────────────────────────────────────────────────────────────────

def _scenario_ids(world_id: str) -> list[str]:
    """월드의 시나리오 id 목록(None 제외). 단일 월드면 빈 리스트."""
    return [info["scenario_id"] for info in scenario_infos(world_id) if info.get("scenario_id")]


def _plan(world_id: str, char_id: str):
    """소스를 분석해 (path, lines, cls, method, ex, scenario_ids) 또는 오류 dict 를 반환합니다."""
    path = se.find_character_file(world_id, char_id)
    if path is None:
        return _fail("캐릭터 소스 파일을 찾지 못했습니다 (먼저 자동 생성이 필요합니다).")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = se._find_character_class(tree, char_id)
    if cls is None:
        return _fail(f"'{char_id}' 클래스를 찾지 못했습니다.")

    # 이미 cfg 패턴이면 변환 불필요.
    existing_cfg = se._class_attr_node(cls, "DEFAULT_CFG")
    if existing_cfg is not None and not (isinstance(existing_cfg, ast.Dict) and not existing_cfg.keys):
        return _fail("이미 DEFAULT_CFG 를 정의한 캐릭터입니다 (변환 불필요/지원 안 함).")

    method = se._find_method(cls, "build_schema")
    if method is None:
        return _fail("build_schema 메서드가 없습니다.")

    scenario_ids = _scenario_ids(world_id)
    ex = _Extracted()

    # Character 생성 구문 소비 (base 가 동일하게 생성하므로 잔여로 남기면 PK 중복).
    char_create_found = False
    for stmt in method.body:
        if _is_character_create_stmt(stmt):
            ex.consumed.add(id(stmt))
            char_create_found = True
    if not char_create_found:
        return _fail("build_schema 에서 Character 생성 구문을 찾지 못했습니다 (지원 안 하는 형태).")

    # blob 3종 + state 추출.
    for label, section in _LABEL_SECTION.items():
        reason = _extract_blob(method, ex, label, section, scenario_ids)
        if reason:
            return _fail(reason)
    reason = _extract_state(method, ex, scenario_ids)
    if reason:
        return _fail(reason)

    if not ex.default_cfg:
        return _fail("cfg 로 추출할 프로파일을 찾지 못했습니다 (지원 안 하는 형태).")

    # 잔여 문장 = 소비되지 않은 본문. docstring/bare-string(no-op)은 새 docstring 으로 대체되므로 제외.
    residual = [s for s in method.body if id(s) not in ex.consumed and not _is_bare_string(s)]
    # 잔여 문장 검증: 소비한 로컬 변수(_state/info_props 등)를 잔여가 참조하면 거부.
    for stmt in residual:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and node.id in ex.assigned_names and isinstance(node.ctx, ast.Load):
                return _fail(f"잔여 구문이 '{node.id}' 를 참조합니다; 소스에서 편집하세요")

    # 빈 override 정리.
    ex.overrides = {sid: sect for sid, sect in ex.overrides.items() if sect}

    return path, source, cls, method, ex, residual


def _rewrite(source: str, method: ast.FunctionDef, ex: _Extracted, residual: list[ast.stmt]) -> str:
    """build_schema 를 cfg 어트리뷰트 + super() 위임 + 잔여 본문으로 교체한 새 소스를 만듭니다."""
    lines = source.splitlines(keepends=True)
    start = method.lineno - 1  # 0-indexed (def 줄)
    if method.decorator_list:
        start = min(d.lineno for d in method.decorator_list) - 1
    end = method.end_lineno     # 0-indexed exclusive

    cfg_src = se._emit(ex.default_cfg, "    ")
    ovr_src = se._emit(ex.overrides, "    ")

    block: list[str] = []
    block.append(f"    DEFAULT_CFG: dict = {cfg_src}\n")
    block.append(f"    SCENARIO_OVERRIDES: dict[str, dict] = {ovr_src}\n")
    block.append("\n")
    block.append("    def build_schema(self, conn: kuzu.Connection) -> None:\n")
    block.append('        """캐릭터 노드와 4-tier 프로파일을 self.cfg 기반으로 생성합니다. (world_editor 변환)"""\n')
    block.append("        super().build_schema(conn)\n")
    for stmt in residual:
        seg = lines[stmt.lineno - 1: stmt.end_lineno]  # 원본 들여쓰기(8칸) 유지
        block.extend(seg)
        if not block[-1].endswith("\n"):
            block[-1] += "\n"

    new_lines = lines[:start] + block + lines[end:]
    return "".join(new_lines)


def _char_snapshot(world_id: str, char_id: str, scenario_ids: list[str]) -> dict:
    """각 시나리오에서 char 노드 + 관련 관계를 추출해 비교용 스냅샷을 만듭니다."""
    sids: list[str | None] = list(scenario_ids) or [None]
    snap: dict = {}
    for sid in sids:
        compiler.invalidate(world_id)
        graph = compiler.compile_world_graph(world_id, sid, use_cache=False)
        char = next((c for c in graph["characters"] if c.get("id") == char_id), None)
        rels = sorted(
            (r for r in graph["relationships"] if char_id in (r.get("source"), r.get("target"))),
            key=lambda r: (r.get("source"), r.get("target"), r.get("type")),
        )
        snap[str(sid)] = {"char": char, "rels": rels}
    return snap


def analyze_character(world_id: str, char_id: str) -> dict:
    """변환 가능성과 추출될 cfg/override/잔여 본문을 미리 보여줍니다 (디스크 무변경)."""
    plan = _plan(world_id, char_id)
    if isinstance(plan, dict):
        return plan
    _path, _source, _cls, _method, ex, residual = plan
    return {
        "ok": True,
        "migratable": True,
        "message": "변환 가능합니다.",
        "default_cfg": ex.default_cfg,
        "scenario_overrides": ex.overrides,
        "residual_lines": len(residual),
    }


def migrate_character(world_id: str, char_id: str, apply: bool = False) -> dict:
    """캐릭터를 cfg 패턴으로 변환합니다. verify-by-recompile 통과 시에만 적용/preview 합니다.

    apply=False: 검증까지 수행하고 diff 를 반환하되 디스크는 원상 복구합니다.
    apply=True : 검증 통과 시 변환을 유지합니다. 실패 시 .bak 으로 복원합니다.
    """
    plan = _plan(world_id, char_id)
    if isinstance(plan, dict):
        return plan
    path, source, _cls, method, ex, residual = plan
    new_source = _rewrite(source, method, ex, residual)

    # 변환 결과가 파싱되는지 먼저 확인.
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        return _fail(f"변환 결과가 파싱되지 않습니다: {e}")

    diff = "".join(difflib.unified_diff(
        source.splitlines(keepends=True), new_source.splitlines(keepends=True),
        fromfile=f"{char_id}.py (before)", tofile=f"{char_id}.py (after)",
    ))

    scenario_ids = _scenario_ids(world_id)
    before = _char_snapshot(world_id, char_id, scenario_ids)

    backup = se._safe_write(path, new_source)  # .bak 백업 + atomic write
    try:
        after = _char_snapshot(world_id, char_id, scenario_ids)
    except Exception as e:  # 변환 소스가 컴파일조차 안 되면 복원.
        _restore(path, backup)
        return _fail(f"변환 후 컴파일 실패: {e}", diff=diff)

    if before != after:
        mismatched = [sid for sid in before if before[sid] != after.get(sid)]
        _restore(path, backup)
        return _fail(
            f"변환 전후 그래프가 달라 적용하지 않았습니다 (시나리오: {', '.join(mismatched)}). "
            f"이 캐릭터는 자동 변환이 안전하지 않습니다; 소스에서 편집하세요.",
            diff=diff,
        )

    if not apply:
        _restore(path, backup)  # preview: 검증만 하고 원복.
        return {"ok": True, "migratable": True, "applied": False, "verified": True,
                "message": "검증 통과 — 적용 시 동일한 그래프가 보장됩니다.", "diff": diff}

    compiler.invalidate(world_id)
    return {"ok": True, "migratable": True, "applied": True, "verified": True,
            "message": f"'{char_id}' 를 cfg 패턴으로 변환했습니다.", "diff": diff, "backup": backup}


def _restore(path, backup: str) -> None:
    """백업(.bak)을 원본 위치로 되돌리고 백업 파일을 정리합니다 (preview/실패 = 순변경 없음)."""
    import os
    import shutil
    shutil.copy2(backup, path)
    try:
        os.remove(backup)
    except OSError:
        pass
    compiler.invalidate()
