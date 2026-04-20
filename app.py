# app.py
"""
GraphRAG 기반 동적 롤플레이 챗봇 - Chainlit 메인 앱

실행: chainlit run app.py
"""

import asyncio
import logging
import os
from datetime import datetime

# ── 불필요한 로그 억제 ─────────────────────────────────────
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

import anthropic
import chainlit as cl

from src.agents.manager_agent import run_manager
from src.ooc.ooc_parser import is_ooc, parse_ooc
from src.updater.state_updater import process_actor_response
from src.updater.complex_updater import delegate_complex_update

# ── OOC physical 변화 → 이벤트 생성 헬퍼 ──────────────
async def _trigger_ooc_event(ooc_text: str, npc_id: str, pc_id: str, changes: dict) -> None:
    try:
        await delegate_complex_update(
            actor_response=ooc_text,
            npc_id=npc_id,
            pc_id=pc_id,
            initial_changes=changes,
            event_only=True,   # OOC: 이벤트 생성만, state/relationship 변경 스킵
        )
    except Exception as e:
        print(f"[OOC event] ERROR: {e}")

# ── 설정 ──────────────────────────────────────────────────
PC_ID  = "sian"
NPC_ID = "eun_seo"

MAX_HISTORY_TURNS  = 10   # 대화 기록 최대 보존 턴 수
RECENT_STORY_TURNS = 3    # recent_story에 포함할 직전 응답 수

# AsyncAnthropic 클라이언트는 모듈 레벨에서 한 번만 생성
_async_client = anthropic.AsyncAnthropic()


# ════════════════════════════════════════════════════════════
# 세션 상태 초기화
# ════════════════════════════════════════════════════════════

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("conversation_history", [])
    cl.user_session.set("current_dt", datetime.now())
    cl.user_session.set("recent_responses", [])

    await cl.Message(
        content=(
            "**GraphRAG 롤플레이 시작**\n\n"
            "- 일반 입력: 롤플레이 진행\n"
            "- `*` 로 시작: OOC 명령 "
            "(예: `*3시간 후.`, `*장소: 헬스장`, `*은서 기분: 화남`)\n"
            "---"
        )
    ).send()


# ════════════════════════════════════════════════════════════
# 메시지 처리
# ════════════════════════════════════════════════════════════

@cl.on_message
async def on_message(message: cl.Message):
    user_input = message.content.strip()
    if not user_input:
        return

    history: list[dict]       = cl.user_session.get("conversation_history")
    current_dt: datetime      = cl.user_session.get("current_dt")
    recent_responses: list[str] = cl.user_session.get("recent_responses")

    # ── OOC 처리 ──────────────────────────────────────────
    if is_ooc(user_input):
        result = await asyncio.to_thread(parse_ooc, user_input, current_dt, NPC_ID)
        current_dt = result["new_dt"]
        cl.user_session.set("current_dt", current_dt)
        await cl.Message(content=f"⚙️ OOC: `{result['summary']}`").send()

        ooc_changes = result.get("state_changes", {})
        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            asyncio.create_task(
                _trigger_ooc_event(user_input, NPC_ID, PC_ID, ooc_changes)
            )

    # ── recent_story 조립 ─────────────────────────────────
    recent_story = "\n".join(recent_responses[-RECENT_STORY_TURNS:])

    # ── Manager 파이프라인 ─────────────────────────────────
    # run_manager는 sync(Neo4j + OpenRouter) → to_thread
    async with cl.Step(name="📊 씬 분류 & 데이터 추출", show_input=False) as step:
        fixed_prompt, genre_prompt, dynamic_prompt, scene_types = \
            await asyncio.to_thread(
                run_manager,
                user_input, PC_ID, NPC_ID, recent_story, current_dt,
            )
        step.output = f"씬 타입: `{scene_types}`"

    # ── Actor 응답 생성 (스트리밍) ─────────────────────────
    response_msg = cl.Message(content="")
    await response_msg.send()

    _model  = os.getenv("MODEL_ACTOR", "claude-haiku-4-5-20251001")
    _system = [{"type": "text", "text": fixed_prompt,
                "cache_control": {"type": "ephemeral"}}]
    if genre_prompt:
        _system.append({"type": "text", "text": genre_prompt})

    full_response = ""
    # AsyncAnthropic + async with → 이벤트 루프 블로킹 없음
    async with _async_client.messages.stream(
        model=_model,
        max_tokens=4096,
        temperature=1.0,
        system=_system,
        messages=[
            *history,
            {"role": "user", "content": dynamic_prompt},
        ],
    ) as stream:
        async for text_chunk in stream.text_stream:
            full_response += text_chunk
            await response_msg.stream_token(text_chunk)

    await response_msg.update()

    # ── 대화 기록 업데이트 ─────────────────────────────────
    history.append({"role": "user",      "content": dynamic_prompt})
    history.append({"role": "assistant", "content": full_response})

    while len(history) > MAX_HISTORY_TURNS * 2:
        history.pop(0)
        history.pop(0)

    cl.user_session.set("conversation_history", history)

    # ── recent_story 업데이트 ─────────────────────────────
    recent_responses.append(full_response[:1500])
    if len(recent_responses) > RECENT_STORY_TURNS:
        recent_responses.pop(0)
    cl.user_session.set("recent_responses", recent_responses)

    # ── 비동기 DB 업데이트 (fire-and-forget) ──────────────
    asyncio.create_task(
        process_actor_response(full_response, NPC_ID, PC_ID)
    )