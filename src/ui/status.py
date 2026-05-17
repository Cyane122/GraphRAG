# ================================
# src/ui/status.py
#
# Chainlit 상태 토스트 커스텀 엘리먼트를 출력합니다.
#
# Functions
#   - send_status_toast(content: str) -> cl.CustomElement : 중앙 상태 토스트 출력
# ================================

import chainlit as cl


async def send_status_toast(content: str) -> cl.CustomElement:
    """일반 메시지 대신 화면 중앙에 잠깐 뜨는 상태 토스트를 출력합니다."""
    toast = cl.CustomElement(
        name="StatusToast",
        props={"content": content},
        display="inline",
    )
    await toast.send(for_id="")
    return toast
