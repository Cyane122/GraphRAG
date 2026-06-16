# ================================
# src/apps/app/output_guard.py
#
# Actor 출력이 별도 금지어 데이터 목록을 위반했는지 검사합니다.
#
# Classes
#   - ForbiddenPattern : 금지어 검사 패턴
#
# Functions
#   - load_forbidden_terms() -> list[ForbiddenPattern] : 금지어 데이터 파일에서 패턴 목록 로드
#   - find_forbidden_terms(text: str) -> list[str] : 텍스트에 포함된 금지어 목록 반환
#   - find_pov_violations(text: str, perspective: int, narrator_name: str | None = None) -> list[str] : 시점 위반 표현 목록 반환
# ================================

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_FORBIDDEN_TERMS_PATH = (
    Path(__file__).resolve().parents[2]
    / "agents"
    / "prompt_factory"
    / "prompts"
    / "blacklist"
    / "FORBIDDEN_TERMS.txt"
)

_FIRST_PERSON_NARRATION_PATTERNS = [
    ("3인칭 지문 1인칭 대명사: 나는", r"(?<![가-힣A-Za-z0-9])나는(?![가-힣A-Za-z0-9])"),
    ("3인칭 지문 1인칭 대명사: 내가", r"(?<![가-힣A-Za-z0-9])내가(?![가-힣A-Za-z0-9])"),
    ("3인칭 지문 1인칭 대명사: 나를", r"(?<![가-힣A-Za-z0-9])나를(?![가-힣A-Za-z0-9])"),
    ("3인칭 지문 1인칭 대명사: 나에게", r"(?<![가-힣A-Za-z0-9])나(?:에게|한테|도|만|는|의|와|랑)(?![가-힣A-Za-z0-9])"),
    ("3인칭 지문 1인칭 대명사: 제/저", r"(?<![가-힣A-Za-z0-9])(?:저는|제가|저를|저에게|저한테|제게)(?![가-힣A-Za-z0-9])"),
    ("3인칭 지문 1인칭 소유: 내", r"(?<![가-힣A-Za-z0-9])내\s+(?:손|발|팔|다리|몸|얼굴|눈|입|목|어깨|가슴|배|허리|등|허벅지|무릎|머리|목소리|시선|숨|생각|기분|마음)"),
]


@dataclass(frozen=True)
class ForbiddenPattern:
    """금지어 검사에 사용할 literal 또는 regex 패턴입니다."""

    label: str
    pattern: str
    mode: str = "literal"

    def matches(self, text: str) -> bool:
        """텍스트가 현재 패턴에 매치되는지 반환합니다."""
        if self.mode == "regex":
            return re.search(self.pattern, text, flags=re.IGNORECASE) is not None
        if self.pattern in text:
            return True
        return re.sub(r"\s+", "", self.pattern) in re.sub(r"\s+", "", text)


@lru_cache(maxsize=1)
def load_forbidden_terms() -> list[ForbiddenPattern]:
    """금지어 데이터 파일에서 literal/regex 패턴을 로드합니다."""
    if not _FORBIDDEN_TERMS_PATH.exists():
        return []

    patterns: list[ForbiddenPattern] = []
    for raw_line in _FORBIDDEN_TERMS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(_parse_forbidden_pattern(line))
    return sorted({item.label: item for item in patterns}.values(), key=lambda item: len(item.label), reverse=True)


def find_forbidden_terms(text: str) -> list[str]:
    """출력 텍스트에 포함된 전역 금지어를 긴 항목 우선으로 반환합니다."""
    if not text:
        return []
    return [item.label for item in load_forbidden_terms() if item.matches(text)]


def find_pov_violations(text: str, perspective: int, narrator_name: str | None = None) -> list[str]:
    """출력 지문/내면 독백에 섞인 시점 위반 표현을 반환합니다."""
    if not text:
        return []

    narration = _remove_quoted_dialogue(text)
    if perspective == 1:
        return _find_first_person_self_name_violations(narration, narrator_name)
    if perspective != 3:
        return []
    hits = [
        label
        for label, pattern in _FIRST_PERSON_NARRATION_PATTERNS
        if re.search(pattern, narration)
    ]
    return hits


def _find_first_person_self_name_violations(narration: str, narrator_name: str | None) -> list[str]:
    """1인칭 지문에서 narrator가 자기 이름으로 지칭된 흔적을 반환합니다."""
    name = str(narrator_name or "").strip()
    if not name:
        return []
    pattern = rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}(?:은|는|이|가|을|를|에게|한테|도|만|의|와|랑)(?![가-힣A-Za-z0-9])"
    if re.search(pattern, narration):
        return [f"1인칭 지문 자기 이름 3인칭화: {name}"]
    return []


def _parse_forbidden_pattern(line: str) -> ForbiddenPattern:
    """금지어 데이터 한 줄을 검사 패턴으로 변환합니다."""
    if line.startswith("regex:"):
        body = line.removeprefix("regex:").strip()
        if "=>" in body:
            label, pattern = (part.strip() for part in body.split("=>", 1))
        else:
            label = pattern = body
        return ForbiddenPattern(label=label, pattern=pattern, mode="regex")
    if line.startswith("literal:"):
        line = line.removeprefix("literal:").strip()
    return ForbiddenPattern(label=line, pattern=line)


def _remove_quoted_dialogue(text: str) -> str:
    """큰따옴표 대사를 제거하고 지문/내면 독백만 검사 대상으로 남깁니다."""
    without_dialogue = re.sub(r'"[^"\n]*(?:"|$)', "", text)
    return re.sub(r"`[^`\n]*(?:`|$)", "", without_dialogue)
