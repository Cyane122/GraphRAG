# app.py
"""
GraphRAG 기반 동적 롤플레이 챗봇 - Chainlit 메인 앱

실행: chainlit run app.py
"""
import asyncio
import logging
import os
import re
import random
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

from src.updater import time_manager

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

load_dotenv()

# ── 감성 문구 풀 세팅 ─────────────────────────────────────
UPDATING_MSGS = [
    "세계의 상태를 갱신하고 있습니다...",
    "세계를 수정하고 있습니다...",
    "흘러간 시간과, 머물다 간 감정들을 기록하고 있습니다...",
    "방금 전의 찰나를 영원한 기억으로 박제하고 있습니다...",
    "당신의 문장이 세계의 밤낮을 조용히 흔들고 있습니다...",
    "방금 전의 찰나를 영원한 기억으로 남기고 있습니다..."
]

UPDATED_MSGS = [
    "세계가 아주 조금, 바뀌었습니다. 당신 덕분에요.",
    "당신 덕에 세계가 조금 달라졌습니다.",
    "운명의 톱니바퀴가 돌아가며, 세계가 새로운 형태를 갖췄습니다.",
    "하나의 페이지가 무사히 넘어갔습니다. 오롯이 당신의 흔적과 함께.",
    "세계의 시곗바늘이 당신의 호흡에 맞춰 다시 움직이기 시작했습니다.",
    "보이지 않는 곳에서, 누군가의 마음이 한 뼘 더 자랐습니다."
]

GENERATING_MSGS = [
    "{char}의 세계를 그려내고 있습니다...",
    "{char}의 세상을 당신과 함께 만들어갑니다...",
    "{char}가 당신의 말을 곱씹고 있습니다..."
]

# ── 설정 ──────────────────────────────────────────────────
PC_ID = "sian"
NPC_ID = "eun_seo"
NPC_NAME_KOR = "은서"  # 문구 출력을 위한 한글 이름 매핑

MAX_HISTORY_TURNS = 10  # 대화 기록 최대 보존 턴 수
RECENT_STORY_TURNS = 3  # recent_story에 포함할 직전 응답 수

# AsyncAnthropic 클라이언트는 모듈 레벨에서 한 번만 생성
_async_client = anthropic.AsyncAnthropic()
load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

time_manager = time_manager.TimeManager(driver=driver)


# ── OOC physical 변화 → 이벤트 생성 헬퍼 ──────────────
async def _trigger_ooc_event(ooc_text: str, npc_id: str, pc_id: str, changes: dict) -> None:
    try:
        await delegate_complex_update(
            actor_response=ooc_text,
            npc_id=npc_id,
            pc_id=pc_id,
            initial_changes=changes,
            event_only=True,
        )
    except Exception as e:
        print(f"[OOC event] ERROR: {e}")


# ════════════════════════════════════════════════════════════
# 세션 상태 초기화
# ════════════════════════════════════════════════════════════

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("conversation_history", [])
    cl.user_session.set("current_dt", datetime.now())
    cl.user_session.set("recent_responses", [])
    cl.user_session.set("pending_commit", None)  # 이전 턴 지연 확정용 컨테이너

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
# 메시지 처리 (메인 루프)
# ════════════════════════════════════════════════════════════

@cl.on_message
async def on_message(message: cl.Message):
    global context_snippet
    user_input = message.content.strip()
    if not user_input:
        return

    # ── 1. 이전 턴 지연 확정 (Deferred Commit) ─────────────────
    # 유저가 새로운 채팅을 쳤다는 것은, 직전 AI 응답을 수락했다는 뜻.
    pending = cl.user_session.get("pending_commit")
    if pending:
        # Step 대신 외부 메시지로 상태 표시
        status_msg = cl.Message(content="", author="기록 보관소")
        await status_msg.send()

        status_msg.content = random.choice(UPDATING_MSGS)
        await status_msg.update()

        # DB 갱신, TimeManager, SNS 시스템 등을 여기서 한꺼번에 처리합니다.
        prev_response = pending["ai_response"]
        await process_actor_response(prev_response, NPC_ID, PC_ID)

        status_msg.content = random.choice(UPDATED_MSGS)
        await status_msg.update()

        # 사용자가 메시지를 읽을 수 있도록 잠시 대기 후 다음 단계 진행
        await asyncio.sleep(1.5)
        await status_msg.remove()
        cl.user_session.set("pending_commit", None)

    # ── 2. 컨텍스트 및 OOC 처리 ─────────────────────────────
    history: list[dict] = cl.user_session.get("conversation_history")
    recent_responses: list[str] = cl.user_session.get("recent_responses")

    context_snippet = ""
    if history:
        context_snippet = history[-1].get("context", "")

    if is_ooc(user_input):
        result = await asyncio.to_thread(parse_ooc, user_input, NPC_ID)
        await cl.Message(content=f"⚙️ OOC: `{result['summary']}`").send()

        ooc_changes = result.get("state_changes", {})
        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            asyncio.create_task(
                _trigger_ooc_event(user_input, NPC_ID, PC_ID, ooc_changes)
            )

    # 외부 메시지로 상태 표시
    time_status_msg = cl.Message(content="", author="세계의 흐름")
    await time_status_msg.send()
    time_status_msg.content = "시간과 공간을 계산하고 있습니다..."
    await time_status_msg.update()

    time_plan = await asyncio.to_thread(
        time_manager.calculate_and_update_time,
        user_input,
        context_snippet,
        PC_ID,
        NPC_ID
    )

    minutes = time_plan.get('elapsed_minutes')
    action = time_plan.get('action_type')
    time_status_msg.content = f"🕒 [{action}] {minutes if minutes else 'N'}분 경과. ({time_plan.get('reason', '')})"
    await time_status_msg.update()

    recent_story = "\n".join(recent_responses[-RECENT_STORY_TURNS:])

    # ── 3. Manager 파이프라인 (씬 분류) ──────────────────────
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed_prompt, genre_prompt, dynamic_prompt, scene_types = \
            await asyncio.to_thread(
                run_manager,
                user_input=user_input, pc_id=PC_ID, npc_id=NPC_ID, recent_story=recent_story, world_id=os.getenv("WORLD_ID")
            )
        step.output = f"씬 타입: `{scene_types}`"

    # ── 4. Actor 응답 생성 (스트리밍 및 UX 연동) ────────────
    response_msg = cl.Message(content="")
    await response_msg.send()

    _model = os.getenv("MODEL_ACTOR", "claude-haiku-4-5-20251001")
    _system = [{"type": "text", "text": fixed_prompt, "cache_control": {"type": "ephemeral"}}]
    if genre_prompt:
        _system.append({"type": "text", "text": genre_prompt})

    raw_response = ""

    gen_status_msg = cl.Message(
        content=random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR),
        author="System"
    )
    await gen_status_msg.send()

    await response_msg.send()

    # <thinking> 태그 처리
    async with cl.Step(name="사고 과정", show_input=False) as gen_step:
        gen_step.output = random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR)

        # 1. 모델의 전체 응답을 먼저 받습니다 (UI 스트리밍 없이).
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
                raw_response += text_chunk

        # 2. <thinking> 블록을 추출하고, 대화 내용에서 제거합니다.
        thinking_content_match = re.search(r"<thinking>(.*?)</thinking>", raw_response, re.DOTALL)

        if thinking_content_match:
            thinking_content = thinking_content_match.group(1).strip()
            # 사고 과정을 Step 내부에 표시
            gen_step.output = f"```thought\n{thinking_content}\n```"
        else:
            gen_step.output = "사고 과정이 생략되었습니다."

        # <thinking> 태그를 제거한 순수 응답 텍스트
        full_response = re.sub(
            r'<thinking>.*?</thinking>\s*',
            '',
            raw_response,
            flags=re.DOTALL
        ).strip()

        # 3. 정리된 텍스트를 사용자에게 "타이핑하듯이" 스트리밍합니다.
        for token in full_response:
            await response_msg.stream_token(token)
            # 실제 스트리밍처럼 보이게 약간의 딜레이를 줍니다.
            await asyncio.sleep(0.005)

    await response_msg.update()

    # ── 5. 대화 기록 업데이트 및 지연 확정 대기 ────────────────
    history.append({"role": "user", "content": dynamic_prompt})
    history.append({"role": "assistant", "content": full_response})

    while len(history) > MAX_HISTORY_TURNS * 2:
        history.pop(0)
        history.pop(0)
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    if len(recent_responses) > RECENT_STORY_TURNS:
        recent_responses.pop(0)
    cl.user_session.set("recent_responses", recent_responses)

    # 비동기 DB 업데이트 대신 다음 턴을 위해 Pending 큐에 넣습니다.
    cl.user_session.set("pending_commit", {
        "user_input": user_input,
        "ai_response": full_response,
        "timestamp": datetime.now()
    })


# ════════════════════════════════════════════════════════════
# 세션 종료 시 잔여 펜딩 데이터 확정
# ════════════════════════════════════════════════════════════

@cl.on_chat_end
async def on_chat_end():
    """사용자가 브라우저 창을 닫거나 새로고침하면 호출됩니다."""
    pending = cl.user_session.get("pending_commit")
    if pending:
        print("[System] 채팅 종료 감지. 유보된 마지막 턴을 DB에 반영합니다.")
        prev_response = pending["ai_response"]

        # UI가 이미 끊어졌으므로 백그라운드 태스크로 즉시 실행합니다.
        asyncio.create_task(process_actor_response(prev_response, NPC_ID, PC_ID))