# ================================
# src/tools/world_editor/source_edit.py
#
# AST 기반으로 월드 소스 .py의 리터럴 데이터 블록만 안전하게 읽고/덮어씁니다.
#
# Functions
#   - find_character_file(world_id: str, char_id: str) -> Path | None : char_id 클래스를 가진 파일 경로 탐색.
#   - annotate_graph(world_id: str, graph: dict) -> None : 그래프 dict에 편집 가능 여부를 in-place 주입.
#   - merge_cfg_dict(base: dict, override: dict) -> dict : Character cfg와 같은 방식으로 재귀 병합.
#   - normalize_state_fields(fields: dict) -> dict : DynamicState scalar 필드를 저장 가능한 값으로 정규화.
#   - normalize_cfg_state_values(values: dict) -> dict : cfg 내부 state scalar 필드를 저장 가능한 값으로 정규화.
#   - edit_relationship(world_id: str, source: str, target: str, rel_type: str | None, affinity: int | None, trust: int | None, current_status: str | None) -> dict : 관계 엣지 튜플 1개 치환.
#   - edit_blob(world_id: str, char_id: str, role: str, props: dict) -> dict : insert_static_inline kwargs 전체 치환.
#   - edit_state(world_id: str, char_id: str, fields: dict) -> dict : build_schema 내 무조건 _state dict 치환.
#   - edit_character_cfg(world_id: str, char_id: str, scope: str, scenario_id: str | None, values: dict) -> dict : DEFAULT_CFG / SCENARIO_OVERRIDES 치환.
#   - edit_tuple_row(world_id: str, kind: str, row_id: str, values: dict) -> dict : schema.py 튜플-행 1개 치환.
#   - edit_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : insert_schedule kwargs 일부 치환.
# ================================

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path

from src.tools.world_editor.worlds import world_pkg_dir

# ──────────────────────────────────────────────────────────────────────
# 상수: role/kind → 템플릿 메타데이터
# ──────────────────────────────────────────────────────────────────────

# blob role → insert_static_inline 4번째 위치 인자(label 문자열).
# compiler._extract 의 static/personality/info 키와 1:1 대응한다.
_ROLE_LABEL: dict[str, str] = {
    "static": "StaticProfile",
    "personality": "Personality",
    "info": "DynamicInformation",
}

# edit_tuple_row 템플릿 컬럼 순서. 편집 시 values dict → 이 순서대로 튜플을 재구성한다.
# arity 가 템플릿과 다르면(예: sunghwa 의 arity-6 location) "non-template shape" 로 거부한다.
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

_STATE_INT_FIELDS: tuple[str, ...] = (
    "stress_level",
    "cycle_day",
    "workplace_stress_level",
    "pregnancy_day",
    "cum_shots_this_cycle",
)
_STATE_INT_DEFAULTS: dict[str, int] = {
    "stress_level": 0,
    "cycle_day": 1,
    "workplace_stress_level": 0,
    "pregnancy_day": 0,
    "cum_shots_this_cycle": 0,
}
_STATE_BOOL_FIELDS: tuple[str, ...] = (
    "has_menstrual_cycle",
    "pregnant",
)


# ──────────────────────────────────────────────────────────────────────
# 순수 텍스트/오프셋 헬퍼 (파일을 건드리지 않음, 문자열만 다룸)
# ──────────────────────────────────────────────────────────────────────


def _line_offsets(text: str) -> list[int]:
    """각 줄(1-based lineno)의 '코드포인트' 시작 절대 인덱스 배열을 만듭니다.

    인덱스 0은 패딩(사용 안 함), 인덱스 i 가 i번째 줄의 시작 인덱스입니다.
    파이썬 문자열 슬라이싱은 코드포인트 기준이므로 여기도 len(코드포인트)을 씁니다.
    """
    # 0번 인덱스는 더미 — lineno 가 1부터 시작하므로 정렬을 맞추기 위함.
    offsets = [0, 0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _byte_col_to_codepoint(line: str, byte_col: int) -> int:
    """한 줄(line)에서 UTF-8 '바이트' 컬럼을 '코드포인트' 컬럼으로 변환합니다.

    ast 의 col_offset/end_col_offset 은 PEP 263 에 따라 UTF-8 바이트 오프셋이다.
    그러나 파이썬 문자열 슬라이싱은 코드포인트 기준이라, 한글처럼 멀티바이트 문자가
    노드 경계 '앞'에 있으면 바이트 컬럼을 그대로 쓰면 슬라이스가 글자 중간을 가른다.
    line 의 앞부분을 UTF-8 로 인코딩해 byte_col 바이트만큼 잘라 다시 디코딩하면
    정확한 코드포인트 개수를 얻는다(= 코드포인트 컬럼).
    """
    # ASCII-only 빠른 경로: 바이트 길이 == 코드포인트 길이면 변환 불필요.
    prefix = line.encode("utf-8")[:byte_col]
    # 멀티바이트 경계를 안전하게 처리 — byte_col 은 항상 문자 경계에 떨어진다
    # (ast 가 토큰 경계만 보고하므로). 그래도 방어적으로 errors="ignore".
    return len(prefix.decode("utf-8", errors="ignore"))


def _node_span(text: str, node: ast.AST, line_offsets: list[int]) -> tuple[int, int]:
    """ast 노드의 (lineno,byte_col)~(end_lineno,end_byte_col)을 절대 코드포인트 (start,end)로 환산합니다.

    핵심 안전장치: col_offset 은 UTF-8 바이트, line_offsets/슬라이싱은 코드포인트이므로
    줄별로 _byte_col_to_codepoint 변환을 거쳐야 한글이 앞에 있어도 글자 중간을 가르지 않는다.
    text 는 원문 — 시작/끝 줄을 떼어 바이트→코드포인트 변환에 사용한다.
    """
    # 시작/끝 줄의 코드포인트 시작 인덱스. 다음 줄 시작 직전까지가 그 줄(개행 포함).
    s_line_start = line_offsets[node.lineno]
    s_line = text[s_line_start:line_offsets[node.lineno + 1]] if node.lineno + 1 < len(line_offsets) else text[s_line_start:]
    e_line_start = line_offsets[node.end_lineno]  # type: ignore[index]
    e_line = text[e_line_start:line_offsets[node.end_lineno + 1]] if node.end_lineno + 1 < len(line_offsets) else text[e_line_start:]  # type: ignore[index]

    start = s_line_start + _byte_col_to_codepoint(s_line, node.col_offset)
    # end_lineno/end_col_offset 은 파이썬 3.8+ 에서 항상 존재한다.
    end = e_line_start + _byte_col_to_codepoint(e_line, node.end_col_offset)  # type: ignore[arg-type]
    return start, end


def _base_indent(text: str, node: ast.AST, line_offsets: list[int]) -> str:
    """노드가 시작하는 줄의 '선행 공백'을 그대로 반환합니다 (멀티라인 재포맷 기준).

    _emit 의 base_indent 로 col_offset 을 그대로 쓰면(특히 관계 값 노드처럼
    `"key": (` 형태) 닫는 괄호가 키 위치까지 밀려 보기 흉해진다. 대신 그 줄의
    실제 들여쓰기(공백/탭)를 기준으로 삼아 깔끔하고 일관된 출력을 만든다.
    들여쓰기는 ASCII 공백이므로 바이트/코드포인트 구분 문제가 없다.
    """
    line_start = line_offsets[node.lineno]
    line = text[line_start:line_offsets[node.lineno + 1]] if node.lineno + 1 < len(line_offsets) else text[line_start:]
    # 선행 공백만 추출 (공백+탭). 첫 비공백 전까지.
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def _replace_node_span(text: str, start: int, end: int, new_src: str) -> str:
    """[start, end) 구간을 new_src 로 치환한 새 문자열을 반환합니다 (순수 함수).

    이 함수가 모든 edit_* 의 실질적 핵심입니다. 노드 경계 밖은 한 글자도
    건드리지 않으므로, 같은 블록 내 다른 리터럴(예: _VOLLEYBALL_RELS)은
    바이트 단위로 보존됩니다.
    """
    return text[:start] + new_src + text[end:]


def _literal_eval_segment(text: str, start: int, end: int) -> object:
    """[start,end) 구간을 떼어내 ast.literal_eval 로 평가합니다.

    '우리가 잡은 노드가 정말 정적 리터럴인가'를 증명하는 안전장치입니다.
    리터럴이 아니면 ast 가 ValueError/SyntaxError 를 던져 호출부가 중단합니다.
    """
    segment = text[start:end]
    return ast.literal_eval(segment)


def _emit(value: object, base_indent: str) -> str:
    """파이썬 리터럴 값을 유효한 소스 코드 문자열로 렌더링합니다 (순수 함수).

    - str/int/float/bool/None: repr 사용. repr 은 py3 에서 한글을 그대로 두고
      개행/따옴표만 escape 하므로 안전합니다.
    - list/tuple/set/dict(str 키): 멀티라인으로 들여쓰기. 튜플과 set은 형을 유지.

    base_indent 는 '여는 괄호가 있는 줄'의 들여쓰기입니다. 자식 항목은
    여기에 4칸을 더합니다.
    """
    # 스칼라: 한 줄로 끝.
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)

    inner = base_indent + "    "  # 자식 항목 들여쓰기

    if isinstance(value, dict):
        # str 키만 허용 — JSON blob/_state/관계 dict 모두 str 키이다.
        if not all(isinstance(k, str) for k in value):
            raise ValueError("dict 키는 모두 문자열이어야 합니다.")
        if not value:
            return "{}"
        lines = ["{"]
        for k, v in value.items():
            lines.append(f"{inner}{repr(k)}: {_emit(v, inner)},")
        lines.append(base_indent + "}")
        return "\n".join(lines)

    if isinstance(value, set):
        if len(value) == 0:
            return "set()"
        return "{" + ", ".join(_emit(item, inner) for item in sorted(value, key=repr)) + "}"

    if isinstance(value, (list, tuple)):
        open_b, close_b = ("[", "]") if isinstance(value, list) else ("(", ")")
        if len(value) == 0:
            # 빈 튜플은 () , 빈 리스트는 [] — 한 줄로.
            return open_b + close_b
        lines = [open_b]
        for item in value:
            lines.append(f"{inner}{_emit(item, inner)},")
        lines.append(base_indent + close_b)
        return "\n".join(lines)

    # 그 외 타입(set 등)은 의도적으로 미지원 — 데이터 블록에 등장하지 않는다.
    raise ValueError(f"지원하지 않는 리터럴 타입: {type(value).__name__}")


def _coerce_state_int_value(field: str, value: object) -> int | object:
    """DynamicState 정수 필드의 빈 문자열과 숫자 문자열을 int 값으로 정규화합니다."""
    if field not in _STATE_INT_FIELDS:
        return value
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return _STATE_INT_DEFAULTS[field]
        try:
            return int(raw)
        except ValueError:
            return value
    return value


def _coerce_state_bool_value(field: str, value: object) -> bool | object:
    """DynamicState boolean 필드의 빈 문자열과 boolean 문자열을 bool 값으로 정규화합니다."""
    if field not in _STATE_BOOL_FIELDS:
        return value
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            return False
        if raw == "true":
            return True
        if raw == "false":
            return False
    return value


def normalize_state_fields(fields: dict) -> dict:
    """DynamicState 저장 payload의 알려진 scalar 필드를 Kuzu 호환 값으로 정리합니다."""
    normalized: dict = {}
    for key, value in fields.items():
        value = _coerce_state_int_value(key, value)
        value = _coerce_state_bool_value(key, value)
        normalized[key] = value
    return normalized


def normalize_cfg_state_values(values: dict) -> dict:
    """DEFAULT_CFG/SCENARIO_OVERRIDES 내부 state 섹션의 scalar 필드를 정규화합니다."""
    normalized = dict(values)
    state_values = normalized.get("state")
    if isinstance(state_values, dict):
        normalized["state"] = normalize_state_fields(state_values)
    return normalized


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


# ──────────────────────────────────────────────────────────────────────
# AST 로케이터 (어디를 고칠지 찾되, 편집 가능성도 함께 판정)
# ──────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────────────


def find_character_file(world_id: str, char_id: str) -> Path | None:
    """char_id 클래스를 정의한 캐릭터 소스 파일 경로를 반환합니다 (없으면 None).

    탐색 범위: <world_pkg>/characters/**.py, <world_pkg>/characters.py, <world_pkg>/schema.py.
    각 파일을 ast 로 파싱해 body 에 `id = "<char_id>"` 할당을 가진 클래스가 있으면 그 파일.
    """
    pkg = world_pkg_dir(world_id)

    # 후보 파일 목록: characters/ 디렉터리 재귀 + 단일 characters.py.
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

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            # 읽기/파싱 실패 파일은 조용히 건너뛴다 — 탐색은 best-effort.
            continue
        if _find_character_class(tree, char_id) is not None:
            return path
    return None


def _eval_tuple_columns(node: ast.AST, kind: str) -> dict | None:
    """clean 리터럴 튜플 노드를 컬럼명→값 dict 로 변환합니다(arity/리터럴 불일치 시 None)."""
    if not _is_clean_literal_node(node):
        return None
    try:
        vals = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    cols = _TUPLE_COLUMNS.get(kind, ())
    if not isinstance(vals, tuple) or len(vals) != len(cols):
        return None
    return dict(zip(cols, vals))


def annotate_graph(world_id: str, graph: dict) -> None:
    """graph dict 를 in-place 로 순회하며 각 항목의 편집 가능 여부를 주입합니다 (절대 raise 안 함).

    relationships/characters/locations/rules 각각에 editable/reason(혹은 edit 서브dict)을
    채웁니다. 어떤 단계에서 예외가 나도 그 항목은 editable=False + 사유로 표시하고 계속 진행합니다.
    """
    pkg = world_pkg_dir(world_id)

    # 캐릭터별로 (소스 파일, 파싱 트리)를 캐시 — 같은 파일을 반복 파싱하지 않기 위함.
    file_cache: dict[str, tuple[Path, ast.Module] | None] = {}

    def _char_tree(cid: str) -> tuple[Path, ast.Module] | None:
        """char_id → (경로, ast.Module). 실패 시 None. 결과를 캐시한다."""
        if cid in file_cache:
            return file_cache[cid]
        result: tuple[Path, ast.Module] | None = None
        try:
            path = find_character_file(world_id, cid)
            if path is not None:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                result = (path, tree)
        except Exception:
            result = None
        file_cache[cid] = result
        return result

    # ── 1. 관계 엣지 ────────────────────────────────────────────────
    for rel in graph.get("relationships", []):
        try:
            source = rel.get("source")
            target = rel.get("target")
            info = _char_tree(source)
            if info is None:
                rel["editable"], rel["reason"] = False, "source character file not found"
                continue
            _, tree = info
            cls = _find_character_class(tree, source)
            method = _find_method(cls, "build_relationship") if cls else None
            if method is None:
                rel["editable"], rel["reason"] = False, "build_relationship not found"
                continue
            dicts = _find_rel_dicts(method)
            node, reason = _rel_value_node_for(dicts, target)
            rel["editable"] = node is not None
            rel["reason"] = "" if node is not None else reason
        except Exception as e:  # 방어적: 어떤 경우에도 그래프 주석은 실패하지 않는다.
            rel["editable"], rel["reason"] = False, f"annotate error: {e}"

    # ── 2. 캐릭터 4-tier blob + state ───────────────────────────────
    for char in graph.get("characters", []):
        # 기본값: 전부 not editable. 아래에서 가능 항목만 True 로 갱신.
        edit = {
            "static": {"editable": False, "reason": "not analyzed"},
            "personality": {"editable": False, "reason": "not analyzed"},
            "info": {"editable": False, "reason": "not analyzed"},
            "state": {"editable": False, "reason": "not analyzed"},
        }
        char["source_file"] = None
        try:
            cid = char.get("id")
            info = _char_tree(cid)
            if info is None:
                for k in edit:
                    edit[k] = {"editable": False, "reason": "character file not found"}
                char["edit"] = edit
                continue
            path, tree = info
            # POSIX 상대 경로 — 월드 패키지 기준 (예: "characters/park_sian.py").
            char["source_file"] = path.relative_to(pkg).as_posix()

            cls = _find_character_class(tree, cid)
            method = _find_method(cls, "build_schema") if cls else None
            if cls is not None:
                char["cfg"] = _character_cfg_meta(cls, graph.get("scenario_id"))
            if method is None:
                for k in edit:
                    edit[k] = {"editable": False, "reason": "build_schema not found"}
                char["edit"] = edit
                continue

            # blob 3종: static/personality/info → 각 label 의 insert_static_inline 검사.
            for role, label in _ROLE_LABEL.items():
                call, reason = _find_blob_call(method, label)
                edit[role] = {"editable": call is not None, "reason": "" if call is not None else reason}

            # 커스텀 슬롯 (EXTRA_SLOTS) 주석.
            try:
                from src.tools.world_editor.worlds import load_world as _lw
                _w, _ = _lw(graph.get("world_id", ""), None)
                _extra_slots = list(getattr(_w, "EXTRA_SLOTS", None) or [])
            except Exception:
                _extra_slots = []
            for _slot in _extra_slots:
                _sid, _lbl = _slot.get("id"), _slot.get("label")
                if _sid and _lbl:
                    edit[_sid] = {"editable": True, "reason": ""}

            # state: build_schema body 직속 무조건 _state dict.
            state_node, state_reason = _find_state_dict(method, graph.get("scenario_id"))
            edit["state"] = {"editable": state_node is not None, "reason": "" if state_node is not None else state_reason}

            # cfg 패턴(DEFAULT_CFG 보유) 캐릭터는 blob/state 가 cfg 로 관리되므로, 편집 불가 사유를
            # 'insert_static_inline 없음' 같은 혼란스러운 메시지 대신 cfg 에디터 안내로 대체한다.
            if (char.get("cfg") or {}).get("default"):
                for role in ("static", "personality", "info", "state"):
                    if role in edit and not edit[role]["editable"]:
                        edit[role] = {"editable": False, "reason": "cfg-managed"}

            # schedules: insert_schedule kwargs 중 정적 리터럴 필드만 편집 가능.
            for schedule in char.get("schedules", []):
                call, reason = _find_schedule_call(method, cid, schedule.get("id", ""))
                if call is None:
                    schedule["edit"] = {"editable": False, "reason": reason, "fields": {}, "locked": {}}
                    continue
                meta = _schedule_edit_meta(call)
                schedule["edit"] = {
                    "editable": bool(meta["fields"]),
                    "reason": "" if meta["fields"] else "no literal editable kwargs",
                    "fields": meta["fields"],
                    "locked": meta["locked"],
                }
        except Exception as e:
            for k in edit:
                edit[k] = {"editable": False, "reason": f"annotate error: {e}"}
            for schedule in char.get("schedules", []):
                schedule["edit"] = {"editable": False, "reason": f"annotate error: {e}", "fields": {}, "locked": {}}
        char["edit"] = edit

    # ── 3. 위치 / 규칙 (schema.py 튜플-행) ──────────────────────────
    schema_tree: ast.Module | None = None
    try:
        schema_path = pkg / "schema.py"
        if schema_path.is_file():
            schema_tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    except Exception:
        schema_tree = None

    for loc in graph.get("locations", []):
        try:
            if schema_tree is None:
                loc["editable"], loc["reason"] = False, "schema.py not found"
                continue
            node, reason = _find_tuple_row(schema_tree, "location", loc.get("id"))
            loc["editable"] = node is not None
            loc["reason"] = "" if node is not None else reason
            # scenarios 는 빌드타임 필터(컴파일된 노드 prop 이 아님) → 소스 튜플에서 읽어 노출한다.
            if node is not None:
                _row = _eval_tuple_columns(node, "location")
                if _row is not None:
                    loc["scenarios"] = _row.get("scenarios", [])
        except Exception as e:
            loc["editable"], loc["reason"] = False, f"annotate error: {e}"

    for rule in graph.get("rules", []):
        try:
            if schema_tree is None:
                rule["editable"], rule["reason"] = False, "schema.py not found"
                continue
            node, reason = _find_tuple_row(schema_tree, "rule", rule.get("id"))
            rule["editable"] = node is not None
            rule["reason"] = "" if node is not None else reason
            if node is not None:
                _row = _eval_tuple_columns(node, "rule")
                if _row is not None:
                    rule["scenarios"] = _row.get("scenarios", [])
        except Exception as e:
            rule["editable"], rule["reason"] = False, f"annotate error: {e}"


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


# insert_schedule 호출에 새로 쓸 수 있는(추가 포함) 필드. material/date/location/status 포함.
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


# ──────────────────────────────────────────────────────────────────────
# 편집 적용 공통 파이프라인 + 재로케이터
# ──────────────────────────────────────────────────────────────────────


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
