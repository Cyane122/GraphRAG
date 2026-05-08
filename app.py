# ================================
# app.py
#
# Chainlit 메인 앱. 세션 초기화, 메시지 루프, OOC 분기, Manager 파이프라인,
# Actor 스트리밍, 지연 확정(Deferred Commit), 리롤/수정을 처리합니다.
#
# Functions
#   - on_chat_start() -> None : 세션 변수 초기화 및 오프닝 씬 출력
#   - on_chat_end() -> None : 미확정 pending 강제 처리
#   - on_message(message: cl.Message) -> None : 메시지 루프 메인 핸들러
#   - on_reroll(action: cl.Action) -> None : 리롤 버튼 콜백
#   - on_edit_response(action: cl.Action) -> None : 수정 버튼 콜백
#   - route_user_input(user_input: str, message: cl.Message) -> TurnInputType : 입력 처리 경로 판별
#   - _send_status_toast(content: str) -> cl.CustomElement : 중앙 상태 토스트 출력
# ================================

import asyncio
import json
import logging
import re
import random
from datetime import datetime
from enum import Enum
from pathlib import Path

from google.genai import types

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

import chainlit as cl

from src.config import PERSPECTIVE, WORLD_ID, MODEL_ACTOR, MAX_TOKEN
from src.agents.manager import run_manager, load_world_instance, commit_manager_effects
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.simulation.state.updater import process_actor_response, delegate_complex_update
from src.core.logging.conversation_logger import append_turn, parse_log_file
from src.core.database.driver import async_driver
from src.core.llm.client import get_client

# ── 메시지 풀 ──────────────────────────────────────────────────
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

MAX_HISTORY_TURNS  = 10
RECENT_STORY_TURNS = 3
_LOGS_DIR          = Path("logs")
_TURN_DEBUG_DIR    = _LOGS_DIR / "turn_debug"

# 응답 헤더에서 시각을 추출 (**YYYY년 MM월 DD일 X요일 HH시 MM분 형식)
_HEADER_HOUR_RE  = re.compile(
    r'\*{1,2}\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*[월화수목금토일]요일\s*(\d{2})시\s*\d{2}분'
)
# </analyze> 없이 종료된 경우 산문 시작점을 날짜 헤더로 탐지
_HEADER_SPLIT_RE = re.compile(r"(?=\*\*\d{4}년)")
_OOC_SPAN_RE     = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)
_LORE_QA_RE      = re.compile(
    r"(설정|세계관|관계|호감도|상태|기억|현재\s*시간|현재\s*장소|캐릭터|프로필|누구|어디|언제|왜|뭐|무엇|알려줘|요약)",
)
_SYSTEM_COMMAND_PREFIXES = ("/", "!")


class TurnInputType(str, Enum):
    """사용자 입력이 통과할 처리 경로를 나타냅니다."""

    ROLEPLAY = "roleplay"
    OOC_PATCH = "ooc_patch"
    LORE_QA = "lore_qa"
    REROLL = "reroll"
    EDIT = "edit"
    SYSTEM_COMMAND = "system_command"
    EMPTY = "empty"

# ── 모듈 레벨 초기화 ──────────────────────────────────────────
_genai_client = get_client()
world         = load_world_instance(WORLD_ID)
world_config  = world.get_full_config(PERSPECTIVE)
PC_ID         = world_config["pc_id"]
NPC_ID        = world_config["npc_id"]
NPC_NAME_KOR  = world_config["npc_name_kor"]

# SSES 전용 스케줄 모듈 — 다른 월드에서는 임포트 자체를 생략
if WORLD_ID == "sses":
    from src.assets.worlds.sses.schedule_generator import (
        check_and_trigger_schedule,
        advance_slot,
    )


# ════════════════════════════════════════════════════════════
# 내부 헬퍼
# ════════════════════════════════════════════════════════════

def _make_actions() -> list[cl.Action]:
    """리롤·수정 버튼 목록을 생성한다. 여러 곳에서 호출하므로 팩토리로 유지."""
    return [
        cl.Action(name="reroll",        label="🔄 다시 쓰기", payload={"action": "reroll"}),
        cl.Action(name="edit_response", label="✏️ 수정",      payload={"action": "edit"}),
    ]


def _strip_ooc_spans(text: str) -> str:
    """별표 OOC 구간을 제거한 뒤 남은 RP 입력을 반환합니다."""
    without_bold = re.sub(r"\*\*.*?\*\*", "", text, flags=re.DOTALL)
    return _OOC_SPAN_RE.sub("", without_bold).strip()


def _looks_like_lore_qa(text: str) -> bool:
    """설정 확인성 질문인지 보수적으로 판별합니다."""
    if not _LORE_QA_RE.search(text):
        return False
    return (
        text.endswith("?")
        or "알려줘" in text
        or "요약" in text
        or text.startswith(("설정", "세계관", "상태", "관계"))
    )


def route_user_input(user_input: str, message: cl.Message) -> TurnInputType:
    """
    사용자 입력의 처리 경로만 판단합니다.

    라우터는 DB write나 LLM 호출을 하지 않습니다. 실제 처리는 on_message의 early return
    분기에서 수행해 메시지 루프를 읽으면 각 입력이 어디로 가는지 바로 보이게 합니다.
    """
    text = user_input.strip()
    if not text and not message.elements:
        return TurnInputType.EMPTY
    if text.startswith("__EDIT__:") or text == "__EDIT_CANCEL__":
        return TurnInputType.EDIT
    if text in {"/reroll", "!reroll", "/retry", "!retry"}:
        return TurnInputType.REROLL
    if text.startswith(_SYSTEM_COMMAND_PREFIXES):
        return TurnInputType.SYSTEM_COMMAND
    if is_ooc(text):
        return TurnInputType.OOC_PATCH if not _strip_ooc_spans(text) else TurnInputType.ROLEPLAY
    if _looks_like_lore_qa(text):
        return TurnInputType.LORE_QA
    return TurnInputType.ROLEPLAY


async def _handle_system_command(user_input: str) -> None:
    """Actor 파이프라인을 거치지 않는 앱 레벨 명령을 처리합니다."""
    command = user_input.strip().lower()
    if command in {"/help", "!help"}:
        await cl.Message(
            content=(
                "- 일반 입력: RP 진행\n"
                "- `*...*`: OOC 상태 패치\n"
                "- `/help`: 명령 도움말\n"
                "- `.md` 파일 드랍: 이전 대화 불러오기"
            ),
            author="시스템",
        ).send()
        return
    if command in {"/status", "!status"}:
        await _send_lore_qa_response(user_input)
        return
    await cl.Message(content=f"알 수 없는 시스템 명령입니다: `{user_input}`", author="시스템").send()


async def _send_lore_qa_response(user_input: str) -> None:
    """설정 확인성 질문에 Actor RP 대신 짧은 시스템 응답을 보냅니다."""
    current_time = await _snapshot_game_time()
    npc_location = await _fetch_character_location_name(NPC_ID)
    pc_location  = await _fetch_character_location_name(PC_ID)
    await cl.Message(
        content=(
            f"**현재 설정 요약**\n"
            f"- 월드: `{WORLD_ID}`\n"
            f"- PC: `{PC_ID}`\n"
            f"- NPC: `{NPC_NAME_KOR}` (`{NPC_ID}`)\n"
            f"- 현재 시간: `{current_time or '알 수 없음'}`\n"
            f"- NPC 위치: `{npc_location or '알 수 없음'}`\n"
            f"- PC 위치: `{pc_location or '알 수 없음'}`\n\n"
            f"질문: {user_input}"
        ),
        author="시스템",
    ).send()


async def _fetch_character_location_name(char_id: str) -> str | None:
    """캐릭터가 연결된 Location 이름을 조회합니다."""
    try:
        async with async_driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)
                RETURN coalesce(l.name, l.id) AS location
                """,
                char_id=char_id,
            )
            row = await result.single()
            return row["location"] if row else None
    except Exception:
        return None


def _hour_from_response(text: str) -> int | None:
    """응답 텍스트의 날짜 헤더에서 시각(0-23)을 파싱한다."""
    m = _HEADER_HOUR_RE.search(text)
    return int(m.group(1)) if m else None


def _hour_from_time_string(time_str: str | None) -> int | None:
    """DB에 저장된 인게임 시간 문자열에서 시각(0-23)을 추출한다."""
    if not time_str:
        return None
    try:
        return datetime.fromisoformat(time_str).hour
    except ValueError:
        m = re.search(r"\b([01]?\d|2[0-3]):\d{2}", time_str)
        return int(m.group(1)) if m else None


async def _inject_time_theme(hour: int | None, for_id: str | None = None) -> None:
    """시각이 변경된 경우에만 TimeTheme 커스텀 엘리먼트를 발행한다."""
    if hour is None:
        return
    last = cl.user_session.get("last_theme_hour", -1)
    if hour == last:
        return
    cl.user_session.set("last_theme_hour", hour)
    await cl.CustomElement(name="TimeTheme", props={"hour": hour}).send(for_id or "")


async def _send_status_toast(content: str) -> cl.CustomElement:
    """일반 메시지 대신 화면 중앙에 잠깐 뜨는 상태 토스트를 출력한다."""
    toast = cl.CustomElement(
        name="StatusToast",
        props={"content": content},
        display="inline",
    )
    await toast.send(for_id="")
    return toast


async def _snapshot_game_time() -> str | None:
    """현재 인게임 시간을 ISO 문자열로 반환한다. 리롤 복원용 스냅샷."""
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
    """리롤 시 Manager가 진행시킨 인게임 시간을 되돌린다."""
    try:
        async with async_driver.session() as session:
            await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) SET gs.currentTime = $t",
                t=time_str,
            )
    except Exception:
        pass


async def _restore_response_msg(msg_id: str, content: str) -> None:
    """수정 완료·취소 시 응답 메시지를 원래 형태(버튼 포함)로 복원한다."""
    msg          = cl.Message(id=msg_id, content=content, author=NPC_NAME_KOR)
    msg.elements = []
    msg.actions  = _make_actions()
    await msg.update()


# ════════════════════════════════════════════════════════════
# 지연 확정 (Deferred Commit)
# ════════════════════════════════════════════════════════════

async def _commit_pending(pending: dict) -> None:
    """
    이전 턴의 Actor 응답을 DB에 반영한다.

    리롤 기능을 위해 실제 쓰기를 다음 턴 시작 시점으로 미룬다.
    상태 업데이트 → 로그 기록 → SSES 스케줄 체크 순으로 처리한다.
    """
    toast = await _send_status_toast(random.choice(UPDATING_MSGS))

    await commit_manager_effects(
        pending.get("manager_effects"),
        pc_id=PC_ID,
        npc_id=NPC_ID,
    )

    ooc_from_pregnancy = await process_actor_response(
        pending["ai_response"], NPC_ID, PC_ID,
        scene_types  = pending.get("scene_types"),
        scene_chars  = pending.get("scene_chars", []),
        world_config = world_config,
    )
    if ooc_from_pregnancy:
        # 임신 이벤트 발생 — 다음 OOC 처리 단계에서 자동 주입
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

    await toast.remove()
    toast = await _send_status_toast(random.choice(UPDATED_MSGS))
    await asyncio.sleep(1.5)
    await toast.remove()


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
    Gemini generate_content_stream으로 Actor 응답을 스트리밍한다.

    - system_instruction: fixed + genre 결합
    - prefill "<analyze>\\n": CoT 생략 방지 — 히스토리가 길어져도 항상 분석 블록 유도
    - thinking_done 플래그: </analyze> 수신 전까지 UI 출력 억제, 이후만 스트리밍
    - 반환: (prose, scene_chars, response_msg, hour)
    """
    system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt

    # Anthropic 포맷({role, content}) → Gemini 포맷({role, parts}) 변환
    gemini_msgs = [
        {
            "role":  "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in history
    ]
    gemini_msgs.append({"role": "user",  "parts": [{"text": dynamic_prompt}]})
    gemini_msgs.append({"role": "model", "parts": [{"text": "<analyze>\n"}]})

    gen_msg      = await _send_status_toast(random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR))
    response_msg = cl.Message(content="", author=NPC_NAME_KOR)

    _PREFILL      = "<analyze>\n"
    raw           = _PREFILL   # prefill 포함 전체 원문
    raw_thinking  = ""
    thinking_buf  = _PREFILL   # </analyze> 수신 대기 버퍼
    thinking_done = False
    first_text    = True

    try:
        async for chunk in await _genai_client.aio.models.generate_content_stream(
            model    = MODEL_ACTOR,
            contents = gemini_msgs,
            config   = types.GenerateContentConfig(
                system_instruction         = system_text,
                max_output_tokens          = MAX_TOKEN,
                temperature                = 1.0,
                thinking_config            = types.ThinkingConfig(thinking_level="MEDIUM"),
                automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True),
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
                    # 분석 블록 종료 이후 → prose 실시간 스트리밍
                    if first_text:
                        await gen_msg.remove()
                        await response_msg.send()
                        first_text = False
                    await response_msg.stream_token(text)
                else:
                    thinking_buf += text
                    if "</analyze>" in thinking_buf:
                        head, tail    = thinking_buf.split("</analyze>", 1)
                        raw_thinking  = re.sub(r"<analyze>\s*", "", head).strip()
                        remainder     = tail.lstrip()
                        thinking_done = True
                        if remainder:
                            if first_text:
                                await gen_msg.remove()
                                await response_msg.send()
                                first_text = False
                            await response_msg.stream_token(remainder)

    except Exception as e:
        print(f"[Actor] 스트리밍 오류: {e}")

    # </analyze> 미도착 fallback — 날짜 헤더(**YYYY년)를 analyze/prose 경계로 사용
    if not thinking_done and thinking_buf:
        m = _HEADER_SPLIT_RE.search(thinking_buf)
        if "</analyze>" in thinking_buf:
            head, tail   = thinking_buf.split("</analyze>", 1)
            raw_thinking = re.sub(r"<analyze>\s*", "", head).strip()
            remainder    = tail.lstrip()
        elif m:
            raw_thinking = re.sub(r"<analyze>\s*", "", thinking_buf[:m.start()]).strip()
            remainder    = thinking_buf[m.start():]
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

    # analyze 블록 제거 후 prose만 추출
    if "</analyze>" in raw:
        prose = re.sub(r"<analyze>.*?</analyze>", "", raw, flags=re.DOTALL).strip()
    else:
        m2    = _HEADER_SPLIT_RE.search(raw)
        prose = raw[m2.start():].strip() if m2 else ""

    _LOGS_DIR.mkdir(exist_ok=True)
    (_LOGS_DIR / "raw_full.txt").write_text(raw,          encoding="utf-8")
    (_LOGS_DIR / "raw_output.txt").write_text(prose,       encoding="utf-8")
    (_LOGS_DIR / "raw_thinking.txt").write_text(raw_thinking, encoding="utf-8")
    print(f"\n{'='*60}\n[Actor Prose]\n{prose[:800]}\n{'='*60}")
    print(f"[Actor Thinking ({len(raw_thinking)}chars)] / prose={len(prose)}chars")

    # thinking 텍스트에서 씬 내 등장인물 추출 (2-4자 한글 이름)
    scene_chars: list[str] = []
    chars_m = re.search(r"CHARACTERS:\s*(\[.*?\])", raw_thinking, re.DOTALL)
    if chars_m:
        try:
            parsed = json.loads(chars_m.group(1))
            scene_chars = [
                c for c in parsed
                if isinstance(c, str) and 2 <= len(c) <= 4 and re.match(r"^[가-힣]+$", c)
            ]
        except Exception:
            pass

    return prose, scene_chars, response_msg, _hour_from_response(prose)


# ════════════════════════════════════════════════════════════
# 로그 파일 복원
# ════════════════════════════════════════════════════════════

async def _load_log_into_session(file_path: Path) -> None:
    """드랍된 .md 로그 파일을 파싱해 대화 히스토리를 세션에 복원한다."""
    turns = parse_log_file(file_path)
    if not turns:
        await cl.Message(content=f"⚠️ `{file_path.name}` 에서 불러올 대화가 없습니다.").send()
        return

    await cl.Message(content=f"📂 `{file_path.name}` — {len(turns)}턴 불러오는 중...").send()
    new_history: list[dict] = []
    new_recent:  list[str]  = []
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
# 수정
# ════════════════════════════════════════════════════════════

async def _apply_edit(edited: str) -> None:
    """
    사용자가 수정한 응답 텍스트를 세션 전체에 반영한다.

    응답 메시지 UI, 대화 히스토리, recent_responses, pending_commit 모두 갱신.
    """
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    msg_id = pending.get("response_msg_id")
    if msg_id:
        await _restore_response_msg(msg_id, edited)

    # 히스토리에서 마지막 assistant 턴을 수정된 내용으로 교체
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
    """수정 취소 — UI만 원래 응답으로 복원하고 세션 데이터는 건드리지 않는다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    msg_id = pending.get("response_msg_id")
    if msg_id:
        await _restore_response_msg(msg_id, pending.get("ai_response", ""))


def _write_turn_debug_snapshot(
    user_input:       str,
    fixed_prompt:     str,
    genre_prompt:     str,
    dynamic_prompt:   str,
    scene_types:      list[str],
    manager_effects:  dict,
    history:          list[dict],
) -> str | None:
    """Actor 호출 직전의 최종 프롬프트와 manager 후보 정보를 파일로 저장합니다."""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        turn_dir = _TURN_DEBUG_DIR / stamp
        turn_dir.mkdir(parents=True, exist_ok=True)

        system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
        final_prompt = (
            "[SYSTEM]\n"
            f"{system_text}\n\n"
            "[HISTORY]\n"
            f"{json.dumps(history, ensure_ascii=False, indent=2)}\n\n"
            "[USER_DYNAMIC_PROMPT]\n"
            f"{dynamic_prompt}\n"
        )

        files = {
            "fixed_prompt.txt":   fixed_prompt,
            "genre_prompt.txt":   genre_prompt or "",
            "dynamic_prompt.txt": dynamic_prompt,
            "final_prompt.txt":   final_prompt,
            "history.json":       json.dumps(history, ensure_ascii=False, indent=2),
            "metadata.json":      json.dumps({
                "timestamp":       stamp,
                "world_id":        WORLD_ID,
                "pc_id":           PC_ID,
                "npc_id":          NPC_ID,
                "npc_name":        NPC_NAME_KOR,
                "scene_types":     scene_types,
                "user_input":      user_input,
                "manager_effects": manager_effects,
                "prompt_lengths": {
                    "fixed":   len(fixed_prompt),
                    "genre":   len(genre_prompt or ""),
                    "dynamic": len(dynamic_prompt),
                    "final":   len(final_prompt),
                },
            }, ensure_ascii=False, indent=2),
        }
        for name, content in files.items():
            (turn_dir / name).write_text(content, encoding="utf-8")

        summary = [
            f"# Turn Debug {stamp}",
            "",
            f"- world: `{WORLD_ID}`",
            f"- pc: `{PC_ID}`",
            f"- npc: `{NPC_NAME_KOR}` (`{NPC_ID}`)",
            f"- scene_types: `{scene_types}`",
            f"- fixed chars: `{len(fixed_prompt)}`",
            f"- genre chars: `{len(genre_prompt or '')}`",
            f"- dynamic chars: `{len(dynamic_prompt)}`",
            f"- final chars: `{len(final_prompt)}`",
            "",
            "## User Input",
            "",
            user_input,
            "",
            "## Time Plan",
            "",
            "```json",
            json.dumps(manager_effects.get("time_plan"), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Pending Effects",
            "",
            "```json",
            json.dumps(manager_effects.get("pending_effects", []), ensure_ascii=False, indent=2),
            "```",
        ]
        (turn_dir / "summary.md").write_text("\n".join(summary), encoding="utf-8")
        return str(turn_dir)
    except OSError as e:
        print(f"[TurnDebug] 저장 실패: {e}")
        return None


# ════════════════════════════════════════════════════════════
# 생성 파이프라인 (on_message / on_reroll 공용)
# ════════════════════════════════════════════════════════════

async def _run_generation(
    user_input:       str,
    history:          list[dict],
    recent_responses: list[str],
    step_suffix:      str = "",
) -> None:
    """
    Manager → Actor 스트리밍 → 히스토리 갱신 → pending_commit 설정까지 한 번에 처리한다.

    on_message와 on_reroll이 동일한 파이프라인을 공유하며,
    step_suffix로 Step 레이블에 "(리롤)" 등을 추가할 수 있다.
    """
    # 1. 현재 인게임 시간 스냅샷 — 리롤 시 복원 기준점
    prev_game_time = await _snapshot_game_time()
    recent_story   = "\n".join(recent_responses[-RECENT_STORY_TURNS:])

    # 2. Manager: 씬 분류 + 시간 계산 + 프롬프트 조립
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed, genre, dynamic, scene_types, manager_effects = await run_manager(
            user_input   = user_input,
            pc_id        = PC_ID,
            npc_id       = NPC_ID,
            recent_story = recent_story,
            world_id     = WORLD_ID,
            perspective  = PERSPECTIVE,
            return_meta  = True,
        )
        suffix      = f" {step_suffix}" if step_suffix else ""
        step.output = f"씬 타입: `{scene_types}`{suffix}"

    debug_dir = _write_turn_debug_snapshot(
        user_input      = user_input,
        fixed_prompt    = fixed,
        genre_prompt    = genre,
        dynamic_prompt  = dynamic,
        scene_types     = scene_types,
        manager_effects = manager_effects,
        history         = history,
    )

    # 3. Actor 스트리밍
    full_response, scene_chars, response_msg, hour = await _stream_actor(
        fixed, genre, dynamic, history
    )
    if hour is None:
        hour = _hour_from_time_string(await _snapshot_game_time())

    # 4. 히스토리 갱신 — 리롤을 위한 스냅샷을 먼저 보존
    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    history += [{"role": "user", "content": dynamic}, {"role": "assistant", "content": full_response}]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])

    # 5. 다음 턴에서 확정될 pending 등록
    cl.user_session.set("pending_commit", {
        "user_input":       user_input,
        "ai_response":      full_response,
        "scene_types":      scene_types,
        "scene_chars":      scene_chars,
        "timestamp":        datetime.now(),
        "history_snapshot": history_snapshot,
        "recent_snapshot":  recent_snapshot,
        "prev_game_time":   prev_game_time,
        "manager_effects":  manager_effects,
        "time_plan":        manager_effects.get("time_plan"),
        "pending_effects":  manager_effects.get("pending_effects", []),
        "pending_state_diff": [],
        "committed_diff":   [],
        "rejected_diff":    [],
        "debug_dir":        debug_dir,
        "response_msg_id":  response_msg.id,
    })

    response_msg.actions = _make_actions()
    await response_msg.update()
    await _inject_time_theme(hour, for_id=response_msg.id)


# ════════════════════════════════════════════════════════════
# 리롤
# ════════════════════════════════════════════════════════════

@cl.action_callback("reroll")
async def on_reroll(action: cl.Action) -> None:
    """
    이전 응답 메시지를 삭제하고 같은 입력으로 재생성한다.

    히스토리와 인게임 시간을 응답 직전 상태로 되돌린 뒤 파이프라인을 재실행한다.
    DB 쓰기는 아직 확정 전(pending)이므로 별도 롤백 없이 스냅샷 복원만으로 충분하다.
    """
    await _reroll_pending_response(action.forId)


async def _reroll_pending_response(message_id: str | None = None) -> None:
    """현재 pending 응답을 버리고 같은 사용자 입력으로 다시 생성합니다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        await cl.Message(content="다시 쓸 pending 응답이 없습니다.", author="시스템").send()
        return

    if message_id:
        await cl.Message(id=message_id, content="").remove()
    elif pending.get("response_msg_id"):
        await cl.Message(id=pending["response_msg_id"], content="").remove()

    # 스냅샷 복원 — Manager가 진행시킨 히스토리·시간 되돌리기
    cl.user_session.set("conversation_history", pending["history_snapshot"])
    cl.user_session.set("recent_responses",     pending["recent_snapshot"])
    cl.user_session.set("pending_commit",        None)

    if pending.get("prev_game_time"):
        await _restore_game_time(pending["prev_game_time"])

    history          = cl.user_session.get("conversation_history")
    recent_responses = cl.user_session.get("recent_responses")
    await _run_generation(pending["user_input"], history, recent_responses, step_suffix="(리롤)")


# ════════════════════════════════════════════════════════════
# 수정 버튼
# ════════════════════════════════════════════════════════════

@cl.action_callback("edit_response")
async def on_edit_response(action: cl.Action) -> None:
    """응답 메시지를 인라인 편집 폼으로 교체한다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    msg_id = pending.get("response_msg_id")
    if not msg_id:
        return

    edit_msg          = cl.Message(id=msg_id, content="", author=NPC_NAME_KOR)
    edit_msg.elements = [
        cl.CustomElement(
            name    = "EditableMessage",
            props   = {"content": pending.get("ai_response", "")},
            display = "inline",
        )
    ]
    edit_msg.actions = []
    await edit_msg.update()


# ════════════════════════════════════════════════════════════
# 세션 초기화 / 종료
# ════════════════════════════════════════════════════════════

@cl.on_chat_start
async def on_chat_start() -> None:
    """세션 변수 초기화 및 오프닝 씬 출력."""
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
    await _inject_time_theme(_hour_from_time_string(await _snapshot_game_time()))


@cl.on_chat_end
async def on_chat_end() -> None:
    """
    채팅 종료 시 미확정 pending을 백그라운드에서 강제 처리한다.

    정상 흐름에서는 다음 턴 시작 시 처리되지만,
    마지막 응답 후 바로 창을 닫는 경우를 대비해 fire-and-forget으로 실행한다.
    """
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    await commit_manager_effects(
        pending.get("manager_effects"),
        pc_id=PC_ID,
        npc_id=NPC_ID,
    )
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
async def on_message(message: cl.Message) -> None:
    """
    메시지 루프 메인 핸들러.

    1. Turn Router가 입력 유형을 판별
    2. 수정/리롤/파일/empty/system 명령은 즉시 처리 후 종료
    3. 확정 가능한 이전 pending을 커밋
    4. OOC-only와 설정 QA는 Actor 파이프라인을 건너뜀
    5. RP 입력만 Manager → Actor → 히스토리 갱신 (_run_generation)
    """
    user_input = message.content.strip()
    route = route_user_input(user_input, message)

    # ── 수정 완료·취소 감지 — CustomElement에서 보내는 특수 접두사 ──
    if route == TurnInputType.EDIT and user_input.startswith("__EDIT__:"):
        await message.remove()
        await _apply_edit(user_input[9:])
        return

    if route == TurnInputType.EDIT and user_input == "__EDIT_CANCEL__":
        await message.remove()
        await _cancel_edit()
        return

    if route == TurnInputType.REROLL:
        await message.remove()
        await _reroll_pending_response()
        return

    # ── 파일 드랍 ────────────────────────────────────────────────
    if message.elements:
        for el in message.elements:
            if isinstance(el, cl.File) and el.name.endswith(".md") and el.path:
                await _load_log_into_session(Path(el.path))
                return

    if route == TurnInputType.EMPTY or not user_input:
        return

    if route == TurnInputType.SYSTEM_COMMAND:
        await _handle_system_command(user_input)
        return

    history          = cl.user_session.get("conversation_history")
    recent_responses = cl.user_session.get("recent_responses")

    # ── 1. 이전 턴 확정 ─────────────────────────────────────────
    pending = cl.user_session.get("pending_commit")
    if pending:
        await _commit_pending(pending)
        cl.user_session.set("pending_commit", None)

    # ── 2. 임신 OOC 자동 주입 ───────────────────────────────────
    pending_ooc = cl.user_session.get("pending_ooc")
    if pending_ooc:
        cl.user_session.set("pending_ooc", None)
        user_input = f"{pending_ooc}\n{user_input}"
        await cl.Message(content=pending_ooc, author="시스템").send()
        route = route_user_input(user_input, message)

    # ── 3. OOC 처리 ─────────────────────────────────────────────
    if is_ooc(user_input):
        ooc_changes: dict = {}
        async with cl.Step(name="⚙️ OOC", show_input=False) as step:
            result      = await parse_ooc(user_input, NPC_ID, NPC_NAME_KOR)
            ooc_changes = result.get("state_changes", {})
            lines       = [f"**{result['summary']}**"]
            if ooc_changes:
                lines += [f"- `{k}` → `{v}`" for k, v in ooc_changes.items()]
            step.output = "\n".join(lines)

        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            # 부상·입원은 Actor 응답 없이 이벤트만 생성 — fire-and-forget
            asyncio.create_task(
                delegate_complex_update(user_input, NPC_ID, PC_ID, ooc_changes, event_only=True)
            )
        if WORLD_ID == "sses" and ooc_changes.get("action_type") == "session_end":
            await advance_slot()

        if route == TurnInputType.OOC_PATCH:
            return

    if route == TurnInputType.LORE_QA:
        await _send_lore_qa_response(user_input)
        return

    # ── 4. 생성 파이프라인 ───────────────────────────────────────
    await _run_generation(user_input, history, recent_responses)
