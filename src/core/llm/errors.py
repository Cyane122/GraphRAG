# ================================
# src/core/llm/errors.py
#
# LLM 호출/파싱 실패를 분류하는 예외 타입을 정의합니다.
# 호출처가 "재시도 가능한 일시 오류"와 "복구 불가 오류"를 구분할 수 있게 합니다.
#
# Classes
#   - LLMError : 모든 LLM 관련 오류의 베이스.
#   - TransientLLMError : 타임아웃/일시적 가용성 문제 — 재시도 가능.
#   - LLMJsonError : LLM 응답에서 유효한 JSON을 추출하지 못함 — 입력 의존, 재시도해도 같을 수 있음.
# ================================


class LLMError(Exception):
    """LLM 호출/파싱 계열 오류의 베이스 예외."""


class TransientLLMError(LLMError, TimeoutError):
    """타임아웃 등 일시적 실패. 재시도가 의미 있는 경우에 사용한다.

    TimeoutError를 상속해, 기존의 `except TimeoutError` 핸들러가 그대로 포착하도록
    하위 호환을 유지한다(client가 재시도 소진 후 이 예외를 던진다).
    """


class LLMJsonError(LLMError):
    """LLM 응답에서 유효한 JSON 구조를 추출하지 못한 경우."""
