# ================================
# src/ui/response_editing.py
#
# Chainlit 응답 메시지의 리롤/수정/삭제 액션과 편집 반영을 처리합니다.
#
# Functions
#   - make_actions() -> list[cl.Action] : 리롤/수정/삭제 액션 생성
#   - restore_response_msg(msg_id: str, content: str, npc_name: str) -> None : 응답 메시지 복원
#   - apply_edit(edited: str, npc_name: str) -> None : 수정 텍스트를 세션에 반영
#   - cancel_edit(npc_name: str) -> None : 수정 UI 취소
#   - show_edit_form(msg_id: str, content: str, npc_name: str) -> None : 인라인 편집 폼 출력
# ================================

import chainlit as cl

from src.ui.pending_store import save_pending_commit


def make_actions() -> list[cl.Action]:
    """리롤·수정·삭제 버튼 목록을 생성합니다."""
    return [
        cl.Action(name="reroll", label="🔄 다시 쓰기", payload={"action": "reroll"}),
        cl.Action(name="edit_response", label="✏️ 수정", payload={"action": "edit"}),
        cl.Action(name="delete_message", label="🗑️ 삭제", payload={"action": "delete"}),
    ]


async def restore_response_msg(msg_id: str, content: str, npc_name: str) -> None:
    """수정 완료·취소 시 응답 메시지를 원래 형태로 복원합니다."""
    msg = cl.Message(id=msg_id, content=content, author=npc_name)
    msg.elements = []
    msg.actions = make_actions()
    await msg.update()


async def apply_edit(edited: str, npc_name: str) -> None:
    """사용자가 수정한 응답 텍스트를 세션 전체에 반영합니다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    msg_id = pending.get("response_msg_id")
    if msg_id:
        await restore_response_msg(msg_id, edited, npc_name)

    history: list[dict] = cl.user_session.get("conversation_history") or []
    for idx in range(len(history) - 1, -1, -1):
        if history[idx]["role"] == "assistant":
            # msg_id 보존 — 삭제 액션이 history 항목을 찾을 수 있어야 한다
            history[idx] = {"role": "assistant", "content": edited, "msg_id": history[idx].get("msg_id")}
            break
    cl.user_session.set("conversation_history", history)

    recent: list[str] = cl.user_session.get("recent_responses") or []
    if recent:
        recent[-1] = edited[:1500]
        cl.user_session.set("recent_responses", recent)

    pending["ai_response"] = edited
    cl.user_session.set("pending_commit", pending)
    save_pending_commit(
        pending,
        cl.user_session.get("world_id") or "",
        cl.user_session.get("pc_id") or "",
        cl.user_session.get("npc_id") or "",
    )


async def cancel_edit(npc_name: str) -> None:
    """수정 취소 시 UI만 원래 응답으로 복원합니다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    msg_id = pending.get("response_msg_id")
    if msg_id:
        await restore_response_msg(msg_id, pending.get("ai_response", ""), npc_name)


async def show_edit_form(msg_id: str, content: str, npc_name: str) -> None:
    """응답 메시지를 인라인 편집 폼으로 교체합니다."""
    edit_msg = cl.Message(id=msg_id, content="", author=npc_name)
    edit_msg.elements = [
        cl.CustomElement(
            name="EditableMessage",
            props={"content": content},
            display="inline",
        )
    ]
    edit_msg.actions = []
    await edit_msg.update()
