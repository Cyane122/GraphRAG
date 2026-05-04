"""
Chainlit 메인 앱.
세션 초기화, 메시지 루프, OOC 분기, Manager 파이프라인,
Actor 스트리밍, 지연 확정(Deferred Commit), 리롤/수정 처리.
_stream_actor에서 인게임 시각을 파싱해 TimeTheme 엘리먼트로 배경 갱신.
PERSPECTIVE 환경변수(1/3)로 1인칭/3인칭 전환.
sses 세계: opening_scene 주입, 인게임 18:00 일정 자동 생성.

- anthropic 의존 완전 제거
- _stream_actor: genai.Client.aio.models.generate_content_stream 기반으로 전환
  * Gemini thinking 파트(thought=True)를 별도 수집 → CHARACTERS 추출에 사용
  * 실제 텍스트 파트는 청크 단위로 UI에 실시간 스트리밍
  * thinking_level=LOW 고정으로 과도한 사고 후 무출력 방지
- 리롤: GlobalState.currentTime 복원(시간 이중진행 방지) + 이전 메시지 삭제.
- 수정(✏️): EditableMessage CustomElement로 메시지 인라인 편집.
"""

import asyncio
import json
import logging
import os
import re
import random
import pathlib
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.genai import types

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

import chainlit as cl

from src.agents.manager_agent import run_manager, load_world_instance
from src.ooc.ooc_parser import is_ooc, parse_ooc
from src.updater.state_updater import process_actor_response
from src.updater.complex_updater import delegate_complex_update
from src.utils.conversation_logger import append_turn, parse_log_file
from src.utils.db_utils import async_driver
from src.utils.llm_utils import get_client

load_dotenv()

PERSPECTIVE = int(os.getenv("PERSPECTIVE", 3))

UPDATING_MSGS = [
    "흘러간 시간과, 머물다 간 감정들을 기록하고 있습니다...",
    "방금 전의 찰나를 영원한 기억으로 박제하고 있습니다...",
    "당신의 문장이 세계의 밤낮을 조용히 흔들고 있습니다...",
    "방금 전의 찰나를 영원한 기억으로 남기고 있습니다...",
]
UPDATED_MSGS = [
    "세계가 아주 조금, 바뀌었습니다. 당신 덕분에요.",
    "운명의 톱니바퀴가 돌아가며, 세계가 새로운 형태를 갖췄습니다.",
    "하나의 페이지가 무사히 넘어갔습니다. 오롯이 당신의 흔적과 함께.",
    "보이지 않는 곳에서, 누군가의 마음이 한 뼘 더 자랐습니다.",
]
GENERATING_MSGS = [
    "{char}의 세계를 그려내고 있습니다...",
    "{char}가 당신의 말을 곱씹고 있습니다...",
    "{char}의 세상을 당신과 함께 만들어갑니다...",
]

_HEADER_HOUR_RE = re.compile(
    r'\*{1,2}\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*[월화수목금토일]요일\s*(\d{2})시\s*\d{2}분'
)

MAX_HISTORY_TURNS  = 10
RECENT_STORY_TURNS = 3

_genai_client = get_client()

WORLD_ID     = os.getenv("WORLD_ID", "babe_univ")
world        = load_world_instance(WORLD_ID)
world_config = world.get_full_config(PERSPECTIVE)
PC_ID        = world_config["pc_id"]
NPC_ID       = world_config["npc_id"]
NPC_NAME_KOR = world_config["npc_name_kor"]

if WORLD_ID == "sses":
    from src.graph.world.sses_schedule_generator import (
        check_and_trigger_schedule,
        advance_slot,
    )


# ════════════════════════════════════════════════════════════
# 인게임 시간 스냅샷 / 복원
# ════════════════════════════════════════════════════════════

async def _snapshot_game_time() -> str | None:
    try:
        async with async_driver.session() as session:
            rec = await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS t"
            )
            row = await rec.single()
            return row["t"] if row else None
    except Exception:
        return None


async def _restore_game_time(time_str: str) -> None:
    try:
        async with async_driver.session() as session:
            await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) SET gs.currentTime = $t",
                t=time_str,
            )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# 헬퍼
# ════════════════════════════════════════════════════════════

def _hour_from_response(text: str) -> int | None:
    m = _HEADER_HOUR_RE.search(text)
    return int(m.group(1)) if m else None


async def _inject_time_theme(hour: int | None, forId: str | None = None) -> None:
    if hour is None:
        return
    last = cl.user_session.get("last_theme_hour", -1)
    if hour == last:
        return
    cl.user_session.set("last_theme_hour", hour)
    await cl.CustomElement(name="TimeTheme", props={"hour": hour}).send(forId)


async def _commit_pending(pending: dict) -> None:
    msg = cl.Message(content=random.choice(UPDATING_MSGS), author="기록 보관소")
    await msg.send()

    ooc_from_pregnancy = await process_actor_response(
        pending["ai_response"], NPC_ID, PC_ID,
        scene_types  = pending.get("scene_types"),
        scene_chars  = pending.get("scene_chars", []),
        world_config = world_config,
    )
    if ooc_from_pregnancy:
        cl.user_session.set("pending_ooc", ooc_from_pregnancy)
    append_turn(
        user_input  = pending["user_input"],
        ai_response = pending["ai_response"],
        timestamp   = pending.get("timestamp"),
    )

    if WORLD_ID == "sses":
        sms = await check_and_trigger_schedule()
        if sms:
            await cl.Message(content=sms, author="사회정서지원과").send()

    msg.content = random.choice(UPDATED_MSGS)
    await msg.update()
    await asyncio.sleep(1.5)
    await msg.remove()


# ════════════════════════════════════════════════════════════
# Actor 스트리밍 (Gemini)
# ════════════════════════════════════════════════════════════

async def _stream_actor(
    fixed_prompt:   str,
    genre_prompt:   str,
    dynamic_prompt: str,
    history:        list[dict],
) -> tuple[str, list[str], cl.Message, int | None]:
    """
    Gemini generate_content_stream 기반 Actor 응답 스트리밍.

    - system_instruction: fixed_prompt + genre_prompt 결합
    - history: Anthropic 포맷({role, content}) → Gemini 포맷({role, parts}) 변환
      (role "assistant" → "model")
    - thinking 파트(thought=True): raw_thinking에 누적 → CHARACTERS 추출
    - text 파트: raw에 누적 + UI 실시간 스트리밍
    - thinking_level=LOW: 과도한 사고로 인한 무출력 방지
    """
    model_name   = os.getenv("MODEL_ACTOR", "gemini-3-flash-preview")
    system_text  = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
    max_tokens   = int(os.getenv("MAX_TOKEN", 4096))  # actor: analyze+prose 합산 → 65% 제한 미적용

    # history 포맷 변환 (Anthropic → Gemini)
    gemini_msgs = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        gemini_msgs.append({"role": role, "parts": [{"text": msg["content"]}]})
    gemini_msgs.append({"role": "user", "parts": [{"text": dynamic_prompt}]})
    # prefill: 모델이 반드시 <analyze>로 시작하게 강제
    # 히스토리가 길어져도 CoT 생략 방지
    gemini_msgs.append({"role": "model", "parts": [{"text": "<analyze>\n"}]})

    gen_msg = cl.Message(
        content=random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR),
        author="System",
    )
    await gen_msg.send()

    # 모델이 <analyze>...</analyze>을 텍스트에 직접 출력함 (네이티브 thought 파트 미사용).
    # thinking 블록이 끝날 때까지 버퍼링 → 이후 prose만 UI에 스트리밍.
    _PREFILL      = "<analyze>\n"
    raw           = _PREFILL    # prefill 포함한 전체 원문
    raw_thinking  = ""          # <analyze> 내용
    thinking_buf  = _PREFILL    # prefill로 이미 <analyze> 시작됨
    thinking_done = False       # </analyze> 수신 완료 여부
    response_msg  = cl.Message(content="", author=NPC_NAME_KOR)
    first_text    = True

    try:
        async for chunk in await _genai_client.aio.models.generate_content_stream(
            model=model_name,
            contents=gemini_msgs,
            config=types.GenerateContentConfig(
                system_instruction = system_text,
                max_output_tokens  = max_tokens,
                temperature        = 1.0,
                thinking_config    = types.ThinkingConfig(thinking_level="MEDIUM"),   # MEDIUM은 내부 사고 토큰 과소비
                automatic_function_calling = types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        ):
            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if not candidate or not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                text = part.text or ""
                if not text:
                    continue
                raw += text

                if thinking_done:
                    # thinking 끝남 → prose 실시간 스트리밍
                    if first_text:
                        await gen_msg.remove()
                        await response_msg.send()
                        first_text = False
                    await response_msg.stream_token(text)
                else:
                    # </analyze> 탐지 대기 중
                    thinking_buf += text
                    if "</analyze>" in thinking_buf:
                        parts = thinking_buf.split("</analyze>", 1)
                        raw_thinking  = re.sub(r"<analyze>\s*", "", parts[0]).strip()
                        remainder     = parts[1].lstrip()
                        thinking_done = True
                        if remainder:
                            if first_text:
                                await gen_msg.remove()
                                await response_msg.send()
                                first_text = False
                            await response_msg.stream_token(remainder)

    except Exception as e:
        print(f"[Actor] 스트리밍 오류: {e}")

    # </analyze> 미도착 fallback
    if not thinking_done and thinking_buf:
        remainder = thinking_buf
        if "</analyze>" in remainder:
            parts        = remainder.split("</analyze>", 1)
            raw_thinking = re.sub(r"<analyze>\s*", "", parts[0]).strip()
            remainder    = parts[1].lstrip()
        else:
            # 닫는 태그 없음 — 시간 헤더(**YYYY년)로 analyze/prose 경계 탐지
            _HEADER_SPLIT = re.compile(r"(?=\*\*\d{4}년)")
            _m = _HEADER_SPLIT.search(thinking_buf)
            if _m:
                raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf[:_m.start()]).strip()
                remainder    = thinking_buf[_m.start():]
            else:
                raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf).strip()
                remainder    = ""
        if remainder:
            if first_text:
                await gen_msg.remove()
                await response_msg.send()
                first_text = False
            await response_msg.stream_token(remainder)

    if first_text:
        await gen_msg.remove()
        await response_msg.send()

    await response_msg.update()

    # prose only: </analyze> 있으면 regex 제거, 없으면 헤더 이후를 prose로
    if "</analyze>" in raw:
        prose = re.sub(r"<analyze>.*?</analyze>", "", raw, flags=re.DOTALL).strip()
    else:
        _m2 = re.search(r"(?=\*\*\d{4}년)", raw)
        prose = raw[_m2.start():].strip() if _m2 else ""

    pathlib.Path("logs").mkdir(exist_ok=True)
    pathlib.Path("logs/raw_full.txt").write_text(raw, encoding="utf-8")      # analyze + prose
    pathlib.Path("logs/raw_output.txt").write_text(prose, encoding="utf-8")  # prose only
    pathlib.Path("logs/raw_thinking.txt").write_text(raw_thinking, encoding="utf-8")
    print(f"\n{'=' * 60}\n[Actor Prose]\n{prose[:800]}\n{'=' * 60}")
    print(f"[Actor Thinking ({len(raw_thinking)}chars)] / prose={len(prose)}chars")

    # CHARACTERS 추출 — thinking 텍스트에서 파싱
    scene_chars: list[str] = []
    chars_m = re.search(r"CHARACTERS:\s*(\[.*?\])", raw_thinking, re.DOTALL)
    if chars_m:
        try:
            parsed = json.loads(chars_m.group(1))
            scene_chars = [
                c for c in parsed
                if isinstance(c, str) and 2 <= len(c) <= 4
                and re.match(r"^[가-힣]+$", c)
            ]
        except Exception:
            pass

    hour = _hour_from_response(prose)
    return prose, scene_chars, response_msg, hour


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
        new_history += [
            {"role": "user",      "content": turn["user_input"]},
            {"role": "assistant", "content": turn["ai_response"]},
        ]
        new_recent.append(turn["ai_response"][:1500])

    cl.user_session.set("conversation_history", new_history[-MAX_HISTORY_TURNS * 2:])
    cl.user_session.set("recent_responses",     new_recent[-RECENT_STORY_TURNS:])
    cl.user_session.set("pending_commit",        None)
    await cl.Message(content=f"✅ {len(turns)}턴 복원 완료.").send()


# ════════════════════════════════════════════════════════════
# 수정 내부 처리
# ════════════════════════════════════════════════════════════

async def _apply_edit(edited: str) -> None:
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    msg_id = pending.get("response_msg_id")
    if msg_id:
        restored = cl.Message(id=msg_id, content=edited, author=NPC_NAME_KOR)
        restored.elements = []
        restored.actions  = [
            cl.Action(name="reroll",        label="🔄 다시 쓰기", payload={"action": "reroll"}),
            cl.Action(name="edit_response", label="✏️ 수정",      payload={"action": "edit"}),
        ]
        await restored.update()

    history: list[dict] = cl.user_session.get("conversation_history")
    for i in range(len(history) - 1, -1, -1):
        if history[i]["role"] == "assistant":
            history[i] = {"role": "assistant", "content": edited}
            break
    cl.user_session.set("conversation_history", history)

    recent: list[str] = cl.user_session.get("recent_responses")
    if recent:
        recent[-1] = edited[:1500]
        cl.user_session.set("recent_responses", recent)

    pending["ai_response"] = edited
    cl.user_session.set("pending_commit", pending)


async def _cancel_edit() -> None:
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    msg_id   = pending.get("response_msg_id")
    original = pending.get("ai_response", "")
    if msg_id:
        restored = cl.Message(id=msg_id, content=original, author=NPC_NAME_KOR)
        restored.elements = []
        restored.actions  = [
            cl.Action(name="reroll",        label="🔄 다시 쓰기", payload={"action": "reroll"}),
            cl.Action(name="edit_response", label="✏️ 수정",      payload={"action": "edit"}),
        ]
        await restored.update()


# ════════════════════════════════════════════════════════════
# 리롤
# ════════════════════════════════════════════════════════════

@cl.action_callback("reroll")
async def on_reroll(action: cl.Action):
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    if action.forId:
        await cl.Message(id=action.forId, content="").remove()

    cl.user_session.set("conversation_history", pending["history_snapshot"])
    cl.user_session.set("recent_responses",     pending["recent_snapshot"])
    cl.user_session.set("pending_commit",        None)

    prev_time = pending.get("prev_game_time")
    if prev_time:
        await _restore_game_time(prev_time)

    user_input        = pending["user_input"]
    history:          list[dict] = cl.user_session.get("conversation_history")
    recent_responses: list[str]  = cl.user_session.get("recent_responses")

    recent_story = "\n".join(recent_responses[-RECENT_STORY_TURNS:])
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input   = user_input,
            pc_id        = PC_ID,
            npc_id       = NPC_ID,
            recent_story = recent_story,
            world_id     = WORLD_ID,
            perspective  = PERSPECTIVE,
        )
        step.output = f"씬 타입: `{scene_types}` (리롤)"

    full_response, scene_chars, response_msg, hour = await _stream_actor(
        fixed, genre, dynamic, history
    )

    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    history += [{"role": "user", "content": dynamic}, {"role": "assistant", "content": full_response}]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])

    cl.user_session.set("pending_commit", {
        "user_input":        user_input,
        "ai_response":       full_response,
        "scene_types":       scene_types,
        "scene_chars":       scene_chars,
        "timestamp":         datetime.now(),
        "history_snapshot":  history_snapshot,
        "recent_snapshot":   recent_snapshot,
        "prev_game_time":    prev_time,
        "response_msg_id":   response_msg.id,
    })

    response_msg.actions = [
        cl.Action(name="reroll",        label="🔄 다시 쓰기", payload={"action": "reroll"}),
        cl.Action(name="edit_response", label="✏️ 수정",      payload={"action": "edit"}),
    ]
    await response_msg.update()
    await _inject_time_theme(hour, forId=response_msg.id)


# ════════════════════════════════════════════════════════════
# 수정 버튼
# ════════════════════════════════════════════════════════════

@cl.action_callback("edit_response")
async def on_edit_response(action: cl.Action):
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    msg_id   = pending.get("response_msg_id")
    original = pending.get("ai_response", "")
    if not msg_id:
        return

    edit_msg = cl.Message(id=msg_id, content="", author=NPC_NAME_KOR)
    edit_msg.elements = [
        cl.CustomElement(
            name    = "EditableMessage",
            props   = {"content": original},
            display = "inline",
        )
    ]
    edit_msg.actions = []
    await edit_msg.update()


# ════════════════════════════════════════════════════════════
# 세션 초기화 / 종료
# ════════════════════════════════════════════════════════════

@cl.on_chat_start
async def on_chat_start():
    opening_scene = world_config.get("opening_scene", "")

    cl.user_session.set("conversation_history", [])
    cl.user_session.set("recent_responses",     [opening_scene] if opening_scene else [])
    cl.user_session.set("pending_commit",        None)
    cl.user_session.set("last_theme_hour",       -1)
    cl.user_session.set("pending_ooc",            None)

    await cl.Message(
        content=(
            "**GraphRAG 롤플레이 시작**\n\n"
            "- 일반 입력: 롤플레이 진행\n"
            "- `*` 로 시작: OOC 명령 (예: `*3시간 후.`, `*장소: 헬스장`)\n"
            "- `.md` 드랍: 이전 대화 불러오기\n---"
        )
    ).send()

    if opening_scene:
        await cl.Message(content=opening_scene, author="세계").send()


@cl.on_chat_end
async def on_chat_end():
    pending = cl.user_session.get("pending_commit")
    if pending:
        asyncio.create_task(
            process_actor_response(
                pending["ai_response"], NPC_ID, PC_ID,
                scene_types  = pending.get("scene_types"),
                scene_chars  = pending.get("scene_chars", []),
                world_config = world_config,
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

    # ── 수정 완료 / 취소 감지 ────────────────────────────────
    if user_input.startswith("__EDIT__:"):
        await message.remove()
        await _apply_edit(user_input[9:])
        return

    if user_input == "__EDIT_CANCEL__":
        await message.remove()
        await _cancel_edit()
        return

    # ── 파일 드랍 ─────────────────────────────────────────────
    if message.elements:
        for el in message.elements:
            if isinstance(el, cl.File) and el.name.endswith(".md"):
                await _load_log_into_session(Path(el.path))
                return

    if not user_input:
        return

    history:          list[dict] = cl.user_session.get("conversation_history")
    recent_responses: list[str]  = cl.user_session.get("recent_responses")

    # ── 1. 이전 턴 확정 ──────────────────────────────────
    pending = cl.user_session.get("pending_commit")
    if pending:
        await _commit_pending(pending)
        cl.user_session.set("pending_commit", None)

    # ── 1.5. 임신 감지 OOC 주입 ──────────────────────────
    pending_ooc = cl.user_session.get("pending_ooc")
    if pending_ooc:
        cl.user_session.set("pending_ooc", None)
        user_input = f"{pending_ooc}\n{user_input}"
        await cl.Message(content=pending_ooc, author="시스템").send()

    # ── 2. OOC ───────────────────────────────────────────
    ooc_changes: dict = {}
    if is_ooc(user_input):
        async with cl.Step(name="⚙️ OOC", show_input=False) as step:
            result      = await parse_ooc(user_input, NPC_ID, NPC_NAME_KOR)
            ooc_changes = result.get("state_changes", {})
            lines       = [f"**{result['summary']}**"]
            if ooc_changes:
                lines += [f"- `{k}` → `{v}`" for k, v in ooc_changes.items()]
            step.output = "\n".join(lines)

        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            asyncio.create_task(
                delegate_complex_update(user_input, NPC_ID, PC_ID, ooc_changes, event_only=True)
            )

        if WORLD_ID == "sses" and ooc_changes.get("action_type") == "session_end":
            await advance_slot()

    # ── 3. 인게임 시간 스냅샷 ────────────────────────────
    prev_game_time = await _snapshot_game_time()

    # ── 4. Manager ───────────────────────────────────────
    recent_story = "\n".join(recent_responses[-RECENT_STORY_TURNS:])
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input   = user_input,
            pc_id        = PC_ID,
            npc_id       = NPC_ID,
            recent_story = recent_story,
            world_id     = WORLD_ID,
            perspective  = PERSPECTIVE,
        )
        step.output = f"씬 타입: `{scene_types}`"

    # ── 5. Actor 스트리밍 ─────────────────────────────────
    full_response, scene_chars, response_msg, hour = await _stream_actor(
        fixed, genre, dynamic, history
    )

    # ── 6. 히스토리 갱신 ─────────────────────────────────
    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    history += [{"role": "user", "content": dynamic}, {"role": "assistant", "content": full_response}]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])

    # ── 7. 지연 확정 예약 ─────────────────────────────────
    cl.user_session.set("pending_commit", {
        "user_input":        user_input,
        "ai_response":       full_response,
        "scene_types":       scene_types,
        "scene_chars":       scene_chars,
        "timestamp":         datetime.now(),
        "history_snapshot":  history_snapshot,
        "recent_snapshot":   recent_snapshot,
        "prev_game_time":    prev_game_time,
        "response_msg_id":   response_msg.id,
    })

    # ── 8. 버튼 ──────────────────────────────────────────
    response_msg.actions = [
        cl.Action(name="reroll",        label="🔄 다시 쓰기", payload={"action": "reroll"}),
        cl.Action(name="edit_response", label="✏️ 수정",      payload={"action": "edit"}),
    ]
    await response_msg.update()

    # ── 9. 시간 기반 배경 테마 ───────────────────────────
    await _inject_time_theme(hour, forId=response_msg.id)