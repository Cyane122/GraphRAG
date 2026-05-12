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
# ================================

import logging
import random
from datetime import datetime
from pathlib import Path

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

import chainlit as cl

from src.config import PERSPECTIVE, WORLD_ID, MODEL_ACTOR, MAX_TOKEN
from src.agents.manager import run_manager, load_world_instance
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.ui.input_routing import TurnInputType, route_user_input
from src.ui.turn_debug import write_turn_debug_snapshot
from src.ui.actor_stream import stream_actor
from src.ui.deferred_commit import commit_pending, commit_pending_if_any
from src.ui.response_editing import (
    apply_edit,
    cancel_edit,
    make_actions,
    show_edit_form,
)
from src.ui.time_state import (
    hour_from_time_string,
    inject_time_theme,
    restore_game_time,
    snapshot_game_time,
)
from src.simulation.state.updater import delegate_complex_update
from src.core.logging.conversation_logger import parse_log_file
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
    current_time = await snapshot_game_time()
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

# ════════════════════════════════════════════════════════════
# 생성 파이프라인 (on_message / on_reroll 공용)
# ════════════════════════════════════════════════════════════

async def _run_generation(
    user_input:       str,
    history:          list[dict],
    recent_responses: list[str],
    step_suffix:      str = "",
    ooc_result:       dict | None = None,
    user_msg_id:      str | None = None,
) -> None:
    """
    Manager → Actor 스트리밍 → 히스토리 갱신 → pending_commit 설정까지 한 번에 처리한다.

    on_message와 on_reroll이 동일한 파이프라인을 공유하며,
    step_suffix로 Step 레이블에 "(리롤)" 등을 추가할 수 있다.
    """
    # 1. 현재 인게임 시간 스냅샷 — 리롤 시 복원 기준점
    prev_game_time = await snapshot_game_time()
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
            suppress_time_plan=bool(ooc_result and ooc_result.get("time_changed")),
        )
        suffix      = f" {step_suffix}" if step_suffix else ""
        step.output = f"씬 타입: `{scene_types}`{suffix}"

    if ooc_result and ooc_result.get("time_changed"):
        manager_effects["ooc_time_patch"] = ooc_result
        manager_effects["time_plan"] = None
        manager_effects["needs_update"] = None
        manager_effects["daily_systems"] = None
        manager_effects["pending_effects"] = [
            effect for effect in manager_effects.get("pending_effects", [])
            if effect.get("type") not in {"global_time_update", "global_weather_update", "location_update"}
        ]

    debug_dir = write_turn_debug_snapshot(
        user_input      = user_input,
        fixed_prompt    = fixed,
        genre_prompt    = genre,
        dynamic_prompt  = dynamic,
        scene_types     = scene_types,
        manager_effects = manager_effects,
        history         = history,
        world_id        = WORLD_ID,
        pc_id           = PC_ID,
        npc_id          = NPC_ID,
        npc_name        = NPC_NAME_KOR,
        logs_dir        = _LOGS_DIR,
        turn_debug_dir  = _TURN_DEBUG_DIR,
    )

    # 3. Actor 스트리밍
    full_response, scene_chars, response_msg, hour = await stream_actor(
        fixed_prompt   = fixed,
        genre_prompt   = genre,
        dynamic_prompt = dynamic,
        history        = history,
        genai_client   = _genai_client,
        model_name     = MODEL_ACTOR,
        max_token      = MAX_TOKEN,
        npc_name       = NPC_NAME_KOR,
        logs_dir       = _LOGS_DIR,
        status_text    = random.choice(GENERATING_MSGS).format(char=NPC_NAME_KOR),
    )
    if hour is None:
        hour = hour_from_time_string(await snapshot_game_time())

    # 4. 히스토리 갱신 — 리롤을 위한 스냅샷을 먼저 보존
    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    history += [{"role": "user", "content": user_input}, {"role": "assistant", "content": full_response}]
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
        "ooc_result":       ooc_result,
        "time_plan":        manager_effects.get("time_plan"),
        "pending_effects":  manager_effects.get("pending_effects", []),
        "pending_state_diff": [],
        "committed_diff":   [],
        "rejected_diff":    [],
        "debug_dir":        debug_dir,
        "user_msg_id":      user_msg_id or cl.user_session.get("reroll_user_msg_id"),
        "response_msg_id":  response_msg.id,
    })
    cl.user_session.set("reroll_user_msg_id", None)

    response_msg.actions = make_actions()
    await response_msg.update()
    await inject_time_theme(hour, for_id=response_msg.id)


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


async def _remove_message_if_present(message_id: str | None) -> None:
    """Chainlit 메시지가 남아 있으면 제거하고, 이미 없으면 무시합니다."""
    if not message_id:
        return
    try:
        await cl.Message(id=message_id, content="").remove()
    except Exception:
        pass


async def _reroll_pending_response(
    message_id: str | None = None,
    replacement_user_input: str | None = None,
) -> None:
    """현재 pending 응답을 버리고 같은 사용자 입력으로 다시 생성합니다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        await cl.Message(content="다시 쓸 pending 응답이 없습니다.", author="시스템").send()
        return

    await _remove_message_if_present(message_id)
    if pending.get("response_msg_id") != message_id:
        await _remove_message_if_present(pending.get("response_msg_id"))

    # 스냅샷 복원 — Manager가 진행시킨 히스토리·시간 되돌리기
    cl.user_session.set("conversation_history", pending["history_snapshot"])
    cl.user_session.set("recent_responses",     pending["recent_snapshot"])
    cl.user_session.set("pending_commit",        None)

    if pending.get("prev_game_time"):
        await restore_game_time(pending["prev_game_time"])

    if replacement_user_input is not None:
        pending["user_input"] = replacement_user_input

    cl.user_session.set("reroll_user_msg_id", pending.get("user_msg_id"))

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

    await show_edit_form(msg_id, pending.get("ai_response", ""), NPC_NAME_KOR)


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
    await inject_time_theme(hour_from_time_string(await snapshot_game_time()))


@cl.on_chat_end
async def on_chat_end() -> None:
    """
    채팅 종료 시 미확정 pending을 세션 종료 전에 강제 처리한다.

    정상 흐름에서는 다음 턴 시작 시 처리되지만,
    마지막 응답 후 바로 창을 닫는 경우에도 후처리가 유실되지 않도록 기다린다.
    """
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    await commit_pending(
        pending=pending,
        world_id=WORLD_ID,
        pc_id=PC_ID,
        npc_id=NPC_ID,
        world_config=world_config,
        scheduler=check_and_trigger_schedule if WORLD_ID == "sses" else None,
        show_toast=False,
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
        await apply_edit(user_input[9:], NPC_NAME_KOR)
        return

    if route == TurnInputType.EDIT and user_input == "__EDIT_CANCEL__":
        await message.remove()
        await cancel_edit(NPC_NAME_KOR)
        return

    if route == TurnInputType.REROLL:
        await message.remove()
        await _reroll_pending_response()
        return

    pending = cl.user_session.get("pending_commit")
    if (
        pending
        and pending.get("user_msg_id")
        and pending.get("user_msg_id") == getattr(message, "id", None)
        and user_input != pending.get("user_input")
    ):
        await _reroll_pending_response(replacement_user_input=user_input)
        return

    await commit_pending_if_any(
        world_id=WORLD_ID,
        pc_id=PC_ID,
        npc_id=NPC_ID,
        world_config=world_config,
        updating_msgs=UPDATING_MSGS,
        updated_msgs=UPDATED_MSGS,
        scheduler=check_and_trigger_schedule if WORLD_ID == "sses" else None,
    )

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

    # ── 2. 임신 OOC 자동 주입 ───────────────────────────────────
    pending_ooc = cl.user_session.get("pending_ooc")
    if pending_ooc:
        cl.user_session.set("pending_ooc", None)
        user_input = f"{pending_ooc}\n{user_input}"
        await cl.Message(content=pending_ooc, author="시스템").send()
        route = route_user_input(user_input, message)

    # ── 3. OOC 처리 ─────────────────────────────────────────────
    ooc_result: dict | None = None
    if is_ooc(user_input):
        ooc_changes: dict = {}
        async with cl.Step(name="⚙️ OOC", show_input=False) as step:
            result      = await parse_ooc(user_input, NPC_ID, NPC_NAME_KOR)
            ooc_result = result
            ooc_changes = result.get("state_changes", {})
            lines       = [f"**{result['summary']}**"]
            if ooc_changes:
                lines += [f"- `{k}` → `{v}`" for k, v in ooc_changes.items()]
            if result.get("time_changed"):
                lines += [
                    f"- `time_before` -> `{result.get('time_before')}`",
                    f"- `time_after` -> `{result.get('time_after')}`",
                    f"- `applied_time_delta_minutes` -> `{result.get('applied_time_delta_minutes')}`",
                    f"- `applied_time_set` -> `{result.get('applied_time_set')}`",
                ]
            step.output = "\n".join(lines)

        if ooc_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            # OOC-only injury updates still need deterministic event persistence before returning.
            await delegate_complex_update(user_input, NPC_ID, PC_ID, ooc_changes, event_only=True)
        if WORLD_ID == "sses" and ooc_changes.get("action_type") == "session_end":
            await advance_slot()

        if route == TurnInputType.OOC_PATCH:
            if not (ooc_result and ooc_result.get("time_changed")):
                return
            user_input = f"{user_input}\n이어간다."
            route = TurnInputType.ROLEPLAY

    if route == TurnInputType.LORE_QA:
        await _send_lore_qa_response(user_input)
        return

    # ── 4. 생성 파이프라인 ───────────────────────────────────────
    await _run_generation(
        user_input,
        history,
        recent_responses,
        ooc_result=ooc_result,
        user_msg_id=getattr(message, "id", None),
    )
