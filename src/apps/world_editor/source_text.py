# ================================
# src/apps/world_editor/source_text.py
#
# Pure text/offset helpers shared by the world-editor source editors: byte<->codepoint
# column mapping, AST node span resolution, span replacement, and literal emission.
#
# Functions
#   - _line_offsets(text: str) -> list[int] : Codepoint start index of each 1-based line
#   - _byte_col_to_codepoint(line: str, byte_col: int) -> int : Convert a UTF-8 byte column to a codepoint column
#   - _node_span(text: str, node: ast.AST, line_offsets: list[int]) -> tuple[int, int] : Absolute codepoint (start, end) of an AST node
#   - _base_indent(text: str, node: ast.AST, line_offsets: list[int]) -> str : Leading whitespace of the node's starting line
#   - _replace_node_span(text: str, start: int, end: int, new_src: str) -> str : Replace [start, end) with new_src
#   - _literal_eval_segment(text: str, start: int, end: int) -> object : ast.literal_eval the [start, end) segment
#   - _emit(value: object, base_indent: str) -> str : Render a Python literal value as source text
# ================================

from __future__ import annotations

import ast

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
