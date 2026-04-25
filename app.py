# app.py
import asyncio
import logging
import os
import re
import random
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

import anthropic
import chainlit as cl

from src.agents.manager_agent import run_manager, load_world_instance
from src.ooc.ooc_parser import is_ooc, parse_ooc
from src.updater.state_updater import process_actor_response
from src.updater.complex_updater import delegate_complex_update
from src.utils.conversation_logger import append_turn, parse_log_file

load_dotenv()

# ── 문구 풀 ──────────────────────────────────────────────
UPDATING_MSGS = [
    "흘러간 시간과, 머물다 간 감정들을 기록하고 있습니다...",
    "방금 전의 찰나를 영원한 기억으로 박제하고 있습니다...",
    "당신의 문장이 세계의 밤낮을 조용히 흔들고 있습니다...",
]
UPDATED_MSGS = [
    "세계가 아주 조금, 바뀌었습니다. 당신 덕분에요.",
    "운명의 톱니바퀴가 돌아가며, 세계가 새로운 형태를 갖췄습니다.",
    "보이지 않는 곳에서, 누군가의 마음이 한 뼘 더 자랐습니다.",
]
GENERATING_MSGS = [
    "{char}의 세계를 그려내고 있습니다...",
    "{char}가 당신의 말을 곱씹고 있습니다...",
]

# ── 설정 ─────────────────────────────────────────────────
MAX_HISTORY_TURNS  = 10
RECENT_STORY_TURNS = 3

_async_client = anthropic.AsyncAnthropic()

WORLD_ID     = os.getenv("WORLD_ID", "babe_univ")
world        = load_world_instance(WORLD_ID)
world_config = world.get_full_config()
PC_ID        = world_config["pc_id"]
NPC_ID       = world_config["npc_id"]
NPC_NAME_KOR = world_config["npc_name_kor"]

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)


# ════════════════════════════════════════════════════════════
# 헬퍼
# ════════════════════════════════════════════════════════════

async def _commit_pending(pending: dict) -> None:
    """이전 턴 DB 확정 + 로그 저장."""
    msg = cl.Message(content=random.choice(UPDATING_MSGS), author="기록 보관소")
    await msg.send()
    await process_actor_response(
        pending["ai_response"], NPC_ID, PC_ID,
        scene_types=pending.get("scene_types"),
    )
    append_turn(
        user_input  = pending["user_input"],
        ai_response = pending["ai_response"],
        timestamp   = pending.get("timestamp"),
    )
    msg.content = random.choice(UPDATED_MSGS)
    await msg.update()
    await asyncio.sleep(1.5)
    await msg.remove()


async def _stream_actor(
    fixed_prompt:  str,
    genre_prompt:  str,
    dynamic_prompt: str,
    history:       list[dict],
) -> str:
    """Actor 스트리밍. 전체 응답 문자열 반환."""
    model   = os.getenv("MODEL_ACTOR", "claude-haiku-4-5-20251001")
    system  = [{"type": "text", "text": fixed_prompt, "cache_control": {"type": "ephemeral"}}]
    if genre_prompt:
        system.append({"type": "text", "text": genre_prompt})

    response_msg = cl.Message(content="")
    await cl.Message(
        content=random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR),
        author="System",
    ).send()
    await response_msg.send()

    raw = ""
    async with cl.Step(name="사고 과정", show_input=False) as step:
        async with _async_client.messages.stream(
            model       = model,
            max_tokens  = 4096,
            temperature = 1.0,
            system      = system,
            messages    = [*history, {"role": "user", "content": dynamic_prompt}],
        ) as stream:
            async for chunk in stream.text_stream:
                raw += chunk

        m = re.search(r"<thinking>(.*?)</thinking>", raw, re.DOTALL)
        step.output = f"```thought\n{m.group(1).strip()}\n```" if m else "사고 과정이 생략되었습니다."

    full = re.sub(r"<thinking>.*?</thinking>\s*", "", raw, flags=re.DOTALL).strip()
    for token in full:
        await response_msg.stream_token(token)
        await asyncio.sleep(0.005)
    await response_msg.update()
    return full


async def _load_log_into_session(file_path: Path) -> None:
    turns = parse_log_file(file_path)
    if not turns:
        await cl.Message(content=f"⚠️ `{file_path.name}` 에서 불러올 대화가 없습니다.").send()
        return

    await cl.Message(content=f"📂 `{file_path.name}` — {len(turns)}턴 불러오는 중...").send()
    new_history, new_recent = [], []
    for turn in turns:
        await cl.Message(content=turn["user_input"],  author="You").send()
        await cl.Message(content=turn["ai_response"], author=NPC_NAME_KOR).send()
        new_history += [{"role": "user", "content": turn["user_input"]},
                        {"role": "assistant", "content": turn["ai_response"]}]
        new_recent.append(turn["ai_response"][:1500])

    cl.user_session.set("conversation_history", new_history[-MAX_HISTORY_TURNS * 2:])
    cl.user_session.set("recent_responses",     new_recent[-RECENT_STORY_TURNS:])
    cl.user_session.set("pending_commit",        None)
    await cl.Message(content=f"✅ {len(turns)}턴 복원 완료.").send()


# ════════════════════════════════════════════════════════════
# 세션 초기화 / 종료
# ════════════════════════════════════════════════════════════

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("conversation_history", [])
    cl.user_session.set("recent_responses",     [])
    cl.user_session.set("pending_commit",        None)
    await cl.Message(
        content=(
            "**GraphRAG 롤플레이 시작**\n\n"
            "- 일반 입력: 롤플레이 진행\n"
            "- `*` 로 시작: OOC 명령 (예: `*3시간 후.`, `*장소: 헬스장`)\n"
            "- `.md` 드랍: 이전 대화 불러오기\n---"
        )
    ).send()


@cl.on_chat_end
async def on_chat_end():
    pending = cl.user_session.get("pending_commit")
    if pending:
        asyncio.create_task(
            process_actor_response(
                pending["ai_response"], NPC_ID, PC_ID,
                scene_types=pending.get("scene_types"),
            )
        )
        append_turn(
            user_input  = pending["user_input"],
            ai_response = pending["ai_response"],
            timestamp   = pending.get("timestamp"),
        )


# ════════════════════════════════════════════════════════════
# 메시지 처리
# ════════════════════════════════════════════════════════════

@cl.on_message
async def on_message(message: cl.Message):
    user_input = message.content.strip()

    if message.elements:
        for el in message.elements:
            if isinstance(el, cl.File) and el.name.endswith(".md"):
                await _load_log_into_session(Path(el.path))
                return

    if not user_input:
        return

    history:          list[dict] = cl.user_session.get("conversation_history")
    recent_responses: list[str]  = cl.user_session.get("recent_responses")

    # 1. 이전 턴 확정
    pending = cl.user_session.get("pending_commit")
    if pending:
        await _commit_pending(pending)
        cl.user_session.set("pending_commit", None)

    # 2. OOC
    if is_ooc(user_input):
        result = await parse_ooc(user_input, NPC_ID, NPC_NAME_KOR)
        await cl.Message(content=f"⚙️ OOC: `{result['summary']}`").send()
        ooc_changes = result.get("state_changes", {})
        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            asyncio.create_task(
                delegate_complex_update(user_input, NPC_ID, PC_ID, ooc_changes, event_only=True)
            )

    # 3. Manager (씬 분류 + 시간 + 욕구 + 프롬프트 조립)
    recent_story = "\n".join(recent_responses[-RECENT_STORY_TURNS:])
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input   = user_input,
            pc_id        = PC_ID,
            npc_id       = NPC_ID,
            recent_story = recent_story,
            world_id     = WORLD_ID,
        )
        step.output = f"씬 타입: `{scene_types}`"

    # 4. Actor 스트리밍
    full_response = await _stream_actor(fixed, genre, dynamic, history)

    # 5. 히스토리 갱신 + 지연 확정 예약
    history += [{"role": "user", "content": dynamic}, {"role": "assistant", "content": full_response}]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])
    cl.user_session.set("pending_commit", {
        "user_input":  user_input,
        "ai_response": full_response,
        "scene_types": scene_types,
        "timestamp":   datetime.now(),
    })