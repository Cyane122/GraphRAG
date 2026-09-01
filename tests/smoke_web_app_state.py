# ================================
# tests/smoke_web_app_state.py
#
# app 상태 로직(모델 정규화/variant 활성화/삭제/preview)을 DB·LLM 없이 검증하는 smoke 검사.
#
# Functions
#   - _check_models() -> None : 모델 정규화와 ConversationState 신규 필드를 검증.
#   - _check_service_state() -> None : activate_variant/delete_message의 pending 정합성을 검증.
#   - main() -> None : 전체 smoke 검사를 실행.
# ================================

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# service 모듈은 import 시 get_client()를 호출하므로, 자격증명 없이 돌도록 stub한다.
import src.core.llm.client as _llm_client  # noqa: E402

_llm_client.get_client = lambda *a, **k: object()


class _FakeStore:
    """ConversationStore 대체 — save 호출만 센다."""

    def __init__(self) -> None:
        self.saves = 0

    def save(self, state: object) -> object:
        """no-op 저장."""
        self.saves += 1
        return state


def _check_models() -> None:
    """Actor 모델 정규화와 ConversationState 신규 필드를 검증한다."""
    from src.apps.app.models import ConversationState, normalize_actor_model

    # DeepSeek은 지원 Actor 모델이므로 그대로 유지된다(저가 모델로 Actor에 사용).
    # Updater 경로에서는 지연 때문에 제외됐지만 Actor 선택지로는 남아 있다.
    assert normalize_actor_model("deepseek-v4-pro") == "deepseek-v4-pro"
    # 정규화는 대소문자를 구분하고 별칭 표가 비어 있어, 다른 표기는 기본값으로 떨어진다.
    assert normalize_actor_model("DeepSeek-V4-Pro") == "gemini-3.1-pro-preview"
    assert normalize_actor_model(None) == "gemini-3.1-pro-preview"
    assert normalize_actor_model("claude-opus-4-8") == "claude-opus-4-8"

    # Chainlit 기능 이식으로 추가된 영속 필드의 기본값.
    state = ConversationState(world_id="w")
    assert state.pending_ooc == ""
    assert state.narrative_turns == []


def _check_service_state() -> None:
    """activate_variant의 pending 동기화와 delete_message의 pending 폐기를 검증한다."""
    import src.apps.app.service as service
    import src.apps.app.message_ops as message_ops
    from src.apps.app.models import ChatMessage, ConversationState, MessageVariant

    # activate_variant/delete_message는 message_ops로 이동했고 pending_store 헬퍼를
    # 직접 import하므로, 파일 I/O를 막으려면 message_ops 쪽 참조를 no-op으로 대체한다.
    message_ops.discard_pending_commit = lambda *a, **k: None
    message_ops.save_pending_commit = lambda *a, **k: None

    # preview_text는 analyze/ooc 블록을 제거한다.
    assert "analyze" not in service.preview_text("<analyze>\nx\n</analyze>\n**머리글**\n본문")

    # activate_variant: 가장 오래된 버전으로 전환하면 표시 내용과 pending.ai_response가 함께 바뀐다(B5).
    msg = ChatMessage(
        id="a1",
        role="assistant",
        content="new",
        variants=[MessageVariant(content="old", created_at=datetime.now())],
    )
    state = ConversationState(world_id="w", messages=[msg])
    state.pending_commit = {"response_msg_id": "a1", "ai_response": "new"}
    message_ops.activate_variant(state, "a1", 0, _FakeStore())
    assert msg.content == "old"
    assert state.pending_commit["ai_response"] == "old"

    # delete_message: 유저 메시지 삭제 시 짝지어진 assistant도 제거되고, 그 pending은 폐기된다.
    user = ChatMessage(id="u1", role="user", content="hi")
    asst = ChatMessage(id="a2", role="assistant", content="resp", parent_user_id="u1")
    state2 = ConversationState(world_id="w", messages=[user, asst])
    state2.pending_commit = {"response_msg_id": "a2"}
    message_ops.delete_message(state2, "u1", _FakeStore())
    assert all(m.id not in {"u1", "a2"} for m in state2.messages)
    assert state2.pending_commit is None


def main() -> None:
    """app 상태 헬퍼 smoke 검사를 실행한다."""
    _check_models()
    _check_service_state()
    print("smoke_app_state: ok")


if __name__ == "__main__":
    main()
