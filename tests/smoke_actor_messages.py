# ================================
# tests/smoke_actor_messages.py
#
# Actor 공급자별 요청 메시지의 마지막 턴 계약을 네트워크 없이 검증합니다.
#
# Functions
#   - _check_gemini_messages() -> None : Gemini 요청이 프리필 지시를 포함한 user 턴으로 끝나는지 검증합니다.
#   - main() -> None : Actor 메시지 smoke 검사를 실행합니다.
# ================================

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.actor import _gemini_messages  # noqa: E402


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


def main() -> None:
    """Actor 메시지 smoke 검사를 실행합니다."""
    _check_gemini_messages()
    print("smoke_actor_messages: ok")


if __name__ == "__main__":
    main()
