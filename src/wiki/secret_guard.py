# ================================
# src/wiki/secret_guard.py
#
# Hidden/suspected Wiki Secret의 실제 내용이 Actor 가시 출력에 직접 누설되는지 검사합니다.
#
# Classes
#   - HiddenSecretLeak : 누설된 Secret ID와 repair 전용 실제 내용
#
# Functions
#   - find_hidden_secret_leaks(text: str, documents: list[WikiDocument]) -> list[HiddenSecretLeak] : 가시 출력의 hidden Secret 누설 목록을 반환합니다.
# ================================

from __future__ import annotations

from dataclasses import dataclass
import re

from src.wiki.models import WikiDocument

_ACTUAL_RE = re.compile(r"(?m)^-\s*실제 내용:\s*(.+?)\s*$")
_STATUS_RE = re.compile(r"(?m)^-\s*상태:\s*(hidden|suspected|revealed)\s*$", re.IGNORECASE)
_PUBLIC_CLUE_RE = re.compile(r"(?m)^-\s*공개 단서:\s*(.+?)\s*$")
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z0-9]{3,}")


@dataclass(frozen=True)
class HiddenSecretLeak:
    """출력 repair에 필요한 Secret 식별자와 private truth를 보관합니다."""

    document_id: str
    actual_content: str


def _normalized(value: str) -> str:
    """대소문자·공백·문장부호 차이를 제거한 비교 문자열을 반환합니다."""
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _tokens(value: str) -> set[str]:
    """비밀 의미 중첩 검사에 사용할 유의미한 한국어·영문 token 집합을 반환합니다."""
    return {token.casefold() for token in _TOKEN_RE.findall(value)}


def _discloses(actual: str, public_clue: str, text: str) -> bool:
    """출력이 actual truth를 직접 또는 높은 token 중첩으로 공개하는지 반환합니다."""
    actual_normalized = _normalized(actual)
    text_normalized = _normalized(text)
    if actual_normalized and actual_normalized in text_normalized:
        return True
    private_tokens = _tokens(actual) - _tokens(public_clue)
    if len(private_tokens) < 3:
        return False
    overlap = private_tokens & _tokens(text)
    return len(overlap) >= 3 and len(overlap) / len(private_tokens) >= 0.75


def find_hidden_secret_leaks(
    text: str,
    documents: list[WikiDocument],
) -> list[HiddenSecretLeak]:
    """모든 hidden/suspected Secret 정본과 비교해 출력 누설을 반환합니다."""
    if not text.strip():
        return []
    leaks: list[HiddenSecretLeak] = []
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "secret":
            continue
        status_match = _STATUS_RE.search(document.content)
        actual_match = _ACTUAL_RE.search(document.content)
        if (
            status_match is None
            or status_match.group(1).casefold() == "revealed"
            or actual_match is None
        ):
            continue
        public_match = _PUBLIC_CLUE_RE.search(document.content)
        actual = actual_match.group(1).strip()
        public_clue = public_match.group(1).strip() if public_match else ""
        if _discloses(actual, public_clue, text):
            leaks.append(
                HiddenSecretLeak(
                    document_id=metadata.id,
                    actual_content=actual,
                )
            )
    return leaks
