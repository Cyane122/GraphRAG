# ================================
# tests/smoke_actor_messages.py
#
# Actor 공급자별 요청 메시지의 마지막 턴 계약을 네트워크 없이 검증합니다.
#
# Functions
#   - _check_gemini_messages() -> None : Gemini 요청이 프리필 지시를 포함한 user 턴으로 끝나는지 검증합니다.
#   - _check_compose_full_response_collapses_doubled_open() -> None : <analyze> 시작 태그가 중복되면 하나로 접히는지 검증합니다.
#   - _check_compose_full_response_leaves_well_formed_unchanged() -> None : 이미 정상 형태인 응답이 바이트 그대로 유지되는지 검증합니다.
#   - _check_compose_full_response_fallback_stays_balanced() -> None : 닫는 태그 누락 폴백 경로가 여전히 균형 잡힌 블록을 만드는지 검증합니다.
#   - main() -> None : Actor 메시지 smoke 검사를 실행합니다.
# ================================

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.actor import _compose_full_response, _gemini_messages  # noqa: E402


def _check_gemini_messages() -> None:
    """Gemini 요청이 프리필 지시를 포함한 user 턴으로 끝나는지 검증합니다."""
    messages = _gemini_messages(
        "현재 턴",
        [
            {"role": "user", "content": "이전 사용자 턴"},
            {"role": "assistant", "content": "이전 모델 턴"},
        ],
    )

    assert [message["role"] for message in messages] == ["user", "model", "user"]
    assert messages[-1]["parts"][0]["text"] == (
        "현재 턴\n\nBegin your response with <analyze>."
    )


def _check_compose_full_response_collapses_doubled_open() -> None:
    """<analyze> 시작 태그가 중복되면 하나로 접히는지 검증합니다.

    seed prefill(_PREFILL)에 모델이 자기 지시대로 또 한 번 <analyze>를 얹은
    상황을 재현한다: raw가 "<analyze>\n<analyze>\n...\n</analyze>\n..." 형태.
    """
    raw = "<analyze>\n<analyze>\n분석 내용\n</analyze>\n본문 프로즈"
    composed = _compose_full_response(raw, "분석 내용", "본문 프로즈", False)

    assert composed == "<analyze>\n분석 내용\n</analyze>\n본문 프로즈"
    assert composed.count("<analyze>") == 1
    assert composed.count("</analyze>") == 1


def _check_compose_full_response_leaves_well_formed_unchanged() -> None:
    """이미 정상 형태인 응답이 바이트 그대로 유지되는지 검증합니다."""
    raw = "<analyze>\n분석 내용\n</analyze>\n본문 프로즈"
    composed = _compose_full_response(raw, "분석 내용", "본문 프로즈", False)

    assert composed == raw
    assert composed.count("<analyze>") == 1
    assert composed.count("</analyze>") == 1


def _check_compose_full_response_fallback_stays_balanced() -> None:
    """닫는 태그 누락 폴백 경로가 여전히 균형 잡힌 블록을 만드는지 검증합니다."""
    composed = _compose_full_response(
        "<analyze>\n분석 내용 (닫는 태그 없음)",
        "분석 내용 (닫는 태그 없음)",
        "본문 프로즈",
        True,
    )

    assert composed == "<analyze>\n분석 내용 (닫는 태그 없음)\n</analyze>\n본문 프로즈"
    assert composed.count("<analyze>") == 1
    assert composed.count("</analyze>") == 1


def main() -> None:
    """Actor 메시지 smoke 검사를 실행합니다."""
    _check_gemini_messages()
    _check_compose_full_response_collapses_doubled_open()
    _check_compose_full_response_leaves_well_formed_unchanged()
    _check_compose_full_response_fallback_stays_balanced()
    print("smoke_actor_messages: ok")


if __name__ == "__main__":
    main()
