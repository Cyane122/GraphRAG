# ================================
# app.py
#
# Chainlit 메인 앱. 세션 초기화, 메시지 루프, OOC 분기, Manager 파이프라인,
# Actor 스트리밍, 지연 확정(Deferred Commit), 리롤/수정/삭제를 처리합니다.
# JSON 데이터 레이어로 채팅방을 영구 저장하며 사이드바에서 목록을 제공합니다.
# 신규 채팅 시 ChatProfile 드롭다운으로 세계관을 선택하고,
# 각 스레드가 독립적인 Kuzu DB(graph/{world_id}/{thread_id}/)를 가집니다.
#
# Functions
#   - set_chat_profiles(current_user: cl.User | None) -> list[cl.ChatProfile] : 세계관 선택 드롭다운
#   - _thread_db_path(thread_id: str) -> str : 스레드별 Kuzu DB 경로 결정
#   - on_chat_start() -> None : 신규 세션 초기화 및 오프닝 씬 출력
#   - on_chat_resume(thread: ThreadDict) -> None : 기존 채팅방 재개 시 세션 복원
#   - _remove_legacy_graph_steps(steps: list[dict]) -> None : 이전 그래프 메시지 제거
#   - on_chat_end() -> None : 미확정 pending 강제 처리
#   - on_message(message: cl.Message) -> None : 메시지 루프 메인 핸들러
#   - _handle_system_command(user_input: str) -> None : help/debug graph 명령 처리
#   - on_reroll(action: cl.Action) -> None : 리롤 버튼 콜백
#   - on_edit_response(action: cl.Action) -> None : 수정 버튼 콜백
#   - on_delete_message(action: cl.Action) -> None : 삭제 버튼 콜백
# ================================

import asyncio
import atexit
import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

import chainlit as cl
from chainlit.types import ThreadDict

from src.config import PERSPECTIVE, WORLD_ID, MODEL_ACTOR, MAX_TOKEN
from src.agents.manager import run_manager, load_world_instance
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.agents.prompt_factory.usernote import build_usernote_block, load_usernote
from src.ui.history import build_history_from_steps
from src.ui.input_routing import TurnInputType, route_user_input
from src.ui.session_models import PendingCommit
from src.ui.turn_debug import write_turn_debug_snapshot
from src.ui.actor_stream import stream_actor
from src.ui.deferred_commit import commit_pending, commit_pending_if_any
from src.ui.debug_graph import send_debug_graph, upsert_debug_graph
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
from src.simulation.state.multi_character import apply_multi_character_state_updates
from src.simulation.state.updater import delegate_complex_update
from src.simulation.systems.needs import ensure_traits_for_characters
from src.core.database import KuzuAsyncDriver
from src.core.database.helpers import load_graph_info
from src.core.llm.client import get_client
from src.core.data_layer import JsonDataLayer

# ── 데이터 레이어 ────────────────────────────────────────────────
@cl.data_layer
def _make_data_layer() -> JsonDataLayer:
    """JSON 파일 기반 데이터 레이어를 반환합니다."""
    return JsonDataLayer()


# ── 인증 (사이드바 스레드 목록 활성화에 필수) ─────────────────────
@cl.password_auth_callback
async def password_auth_callback(username: str, password: str) -> Optional[cl.User]:
    """어떤 사용자 이름/비밀번호로도 로컬 사용자로 인증합니다."""
    return cl.User(identifier="local", metadata={})


# ── 세계관 선택 드롭다운 ─────────────────────────────────────────
def _parse_profile_name(name: str) -> tuple[str, str | None]:
    """'rofan/academy' → ('rofan', 'academy'), 'babe_univ' → ('babe_univ', None)"""
    parts = name.split("/", 1)
    return parts[0], parts[1] if len(parts) > 1 else None


def _discover_world_profiles() -> list[tuple[str, str | None, str]]:
    """src/assets/worlds/*/schema.py 를 스캔해 (world_id, scenario_id, display_name) 목록을 반환합니다.

    SCENARIOS 가 정의된 세계는 시나리오별로 별도 항목을 생성합니다.
    """
    from importlib import import_module as _import

    worlds_dir = Path("src/assets/worlds")
    result: list[tuple[str, str | None, str]] = []
    for schema_path in sorted(worlds_dir.glob("*/schema.py")):
        world_id = schema_path.parent.name
        if world_id == "default":
            continue
        try:
            module = _import(f"src.assets.worlds.{world_id}.schema")
            world  = getattr(module, "world_instance", None)
            if world and world.SCENARIOS:
                for sid, scenario in world.SCENARIOS.items():
                    result.append((world_id, sid, scenario.display_name))
                continue
        except Exception:
            pass
        result.append((world_id, None, world_id))
    return result


def _thread_db_path(thread_id: str) -> str:
    """스레드별 Kuzu DB 경로를 반환합니다.

    기존 배치가 루트/{uuid}/schema를 쓰는 경우를 우선 지원하고,
    현재 JSON data layer 기반 배치에서는 data/threads/{uuid}/schema를 사용합니다.
    """
    root_schema = Path(thread_id) / "schema"
    data_schema = Path("data") / "threads" / thread_id / "schema"
    if root_schema.exists():
        return str(root_schema)
    return str(data_schema)


@cl.set_chat_profiles
async def set_chat_profiles(current_user: cl.User | None) -> list[cl.ChatProfile]:
    """신규 채팅 시작 시 세계관/시나리오를 선택하는 드롭다운을 생성합니다."""
    return [
        cl.ChatProfile(
            name=f"{wid}/{sid}" if sid else wid,
            markdown_description=f"`{display_name}`",
        )
        for wid, sid, display_name in _discover_world_profiles()
    ]


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

_genai_client = get_client()

# db_path → KuzuAsyncDriver: 재연결 시 이전 세션의 파일 락을 해제하기 위한 전역 레지스트리
_ACTIVE_DRIVERS: dict[str, KuzuAsyncDriver] = {}


def _shutdown_all_drivers() -> None:
    for driver in list(_ACTIVE_DRIVERS.values()):
        try:
            driver.close()
        except Exception:
            pass
    _ACTIVE_DRIVERS.clear()


atexit.register(_shutdown_all_drivers)


def _cleanup_old_turn_debug(keep_days: int = 3) -> None:
    """turn_debug 디렉터리에서 keep_days 일보다 오래된 항목을 삭제합니다."""
    import shutil
    from datetime import timedelta
    if not _TURN_DEBUG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    for entry in _TURN_DEBUG_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            entry_dt = datetime.strptime(entry.name[:8], "%Y%m%d")
            if entry_dt < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except ValueError:
            pass


_cleanup_old_turn_debug()


# ── 세션 월드 변수 ────────────────────────────────────────────────

async def _init_session_world(
    world_id: str,
    thread_id: str,
    scenario_id: str | None = None,
    *,
    create_driver: bool = True,
) -> None:
    """세션 변수에 월드 설정과 per-thread Kuzu 드라이버를 설정합니다.

    create_driver=False 이면 world_config만 세팅하고 DB 드라이버는 만들지 않습니다.
    첫 메시지 시점에 _ensure_db_driver()로 지연 생성합니다.
    """
    world_instance = load_world_instance(world_id)
    world_cfg = world_instance.get_full_config(PERSPECTIVE, scenario_id)
    world_cfg["npc_name_map"] = world_instance.get_npc_name_map()

    cl.user_session.set("world_id",     world_id)
    cl.user_session.set("scenario_id",  scenario_id)
    cl.user_session.set("world_config", world_cfg)
    cl.user_session.set("pc_id",        world_cfg["pc_id"])
    cl.user_session.set("npc_id",       world_cfg["npc_id"])
    cl.user_session.set("npc_name_kor", world_cfg["npc_name_kor"])
    cl.user_session.set("db_path",      _thread_db_path(thread_id))

    if create_driver:
        await _open_db_driver(world_id, scenario_id)


async def _open_db_driver(world_id: str, scenario_id: str | None = None) -> None:
    """이전 드라이버를 닫고 새 KuzuAsyncDriver를 세션에 등록합니다.

    같은 db_path를 열려는 이전 세션의 드라이버(재연결 시 남은 파일 락)도 함께 닫습니다.
    락이 즉시 해제되지 않으면 최대 3초간 0.5초 간격으로 재시도합니다.
    """
    old_driver = cl.user_session.get("db_driver")
    if old_driver is not None:
        old_driver.close()
        await asyncio.sleep(0.1)

    db_path = cl.user_session.get("db_path")

    # 전역 레지스트리에 같은 경로의 드라이버가 남아 있으면(이전 세션) 먼저 닫는다
    stale = _ACTIVE_DRIVERS.pop(db_path, None)
    if stale is not None:
        stale.close()

    last_err: Exception | None = None
    for _ in range(7):  # 최대 3초 (0.5s × 6회 대기)
        try:
            driver = KuzuAsyncDriver(db_path, world_id=world_id, scenario_id=scenario_id)
            _ACTIVE_DRIVERS[db_path] = driver
            cl.user_session.set("db_driver", driver)
            return
        except RuntimeError as e:
            if "Could not set lock" not in str(e):
                raise
            last_err = e
            await asyncio.sleep(0.5)

    raise RuntimeError(f"DB 락 해제 실패 ({db_path}): {last_err}")


async def _ensure_db_driver() -> None:
    """db_driver가 없으면 지금 생성합니다 (신규 채팅 첫 메시지 진입 시 호출)."""
    if cl.user_session.get("db_driver") is not None:
        return
    world_id    = cl.user_session.get("world_id")    or WORLD_ID
    scenario_id = cl.user_session.get("scenario_id")
    await _open_db_driver(world_id, scenario_id)
    # 신규 채팅의 첫 메시지 시점에 스레드 메타데이터 저장 (빈 채팅방 사이드바 등록 방지)
    try:
        thread_id = cl.context.session.thread_id
        await cl.data_layer.update_thread(
            thread_id=thread_id,
            metadata={"world_id": world_id, "scenario_id": scenario_id},
        )
    except Exception:
        pass
    await _ensure_session_traits_initialized()


async def _ensure_session_traits_initialized() -> None:
    """현재 DB의 등장인물 일람을 기준으로 traits를 한 번 초기화합니다."""
    if cl.user_session.get("traits_initialized"):
        return
    try:
        graph_info = await load_graph_info()
        characters = graph_info.get("characters", [])
        await ensure_traits_for_characters(characters)
        cl.user_session.set("traits_initialized", True)
    except Exception as e:
        print(f"[TraitsInit] 등장인물 일람 기반 초기화 실패: {e}")


def _wv() -> tuple[str, str, str, str, dict]:
    """(world_id, pc_id, npc_id, npc_name_kor, world_config) 를 반환합니다."""
    return (
        cl.user_session.get("world_id")     or WORLD_ID,
        cl.user_session.get("pc_id")        or "",
        cl.user_session.get("npc_id")       or "",
        cl.user_session.get("npc_name_kor") or "",
        cl.user_session.get("world_config") or {},
    )


# ── SSES 헬퍼 ────────────────────────────────────────────────────

def _get_scheduler(world_id: str):
    """SSES 월드면 check_and_trigger_schedule을 반환하고, 아니면 None을 반환합니다."""
    if world_id != "sses":
        return None
    from src.assets.worlds.sses.schedule_generator import check_and_trigger_schedule  # noqa: PLC0415
    return check_and_trigger_schedule


async def _sses_advance_slot_if_needed(world_id: str, ooc_changes: dict) -> None:
    """SSES 월드에서 세션 종료 OOC 명령 시 슬롯을 진행합니다."""
    if world_id == "sses" and ooc_changes.get("action_type") == "session_end":
        from src.assets.worlds.sses.schedule_generator import advance_slot  # noqa: PLC0415
        await advance_slot()


# ── 시스템 명령 ──────────────────────────────────────────────────

async def _handle_system_command(user_input: str) -> None:
    """Actor 파이프라인을 거치지 않는 앱 레벨 명령을 처리합니다."""
    command = user_input.strip().lower()
    if command in {"/help", "!help"}:
        await cl.Message(
            content=(
                "- 일반 입력: RP 진행\n"
                "- `*...*`: OOC 상태 패치\n"
                "- `/help`: 명령 도움말\n"
                "- `/debug graph`: 현재 장면 중심 그래프 보기\n"
                "- 사이드바: 채팅방 목록 / 이전 대화 재개\n"
                "- `data/threads/{thread_id}/usernote.md`: 유저노트 (파일 직접 편집, 매 턴 자동 반영)"
            ),
            author="시스템",
        ).send()
        return
    if command in {"/debug graph", "!debug graph", "/graph", "!graph"}:
        _, pc_id, npc_id, _, _ = _wv()
        world_id = cl.user_session.get("world_id") or WORLD_ID
        await send_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
        return
    await cl.Message(content=f"알 수 없는 시스템 명령입니다: `{user_input}`", author="시스템").send()


# ════════════════════════════════════════════════════════════
# 생성 파이프라인 (on_message / on_reroll 공용)
# ════════════════════════════════════════════════════════════

async def _compress_narrative(recent_turns: list[dict], npc_id: str, pc_id: str) -> None:
    """Background task: 10턴마다 타임라인 로그 압축."""
    from src.simulation.systems.memory.narrative import compress_to_narrative_log
    current_time_str = await snapshot_game_time()
    current_dt = None
    if current_time_str:
        try:
            current_dt = datetime.fromisoformat(current_time_str)
        except Exception:
            pass
    await compress_to_narrative_log(recent_turns, current_dt, npc_id, pc_id)


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
    첫 번째 RP 턴에서는 스레드 이름을 사용자 입력으로 설정한다.
    """
    world_id, pc_id, npc_id, npc_name_kor, _ = _wv()

    # 1. 현재 인게임 시간 스냅샷 — 리롤 시 복원 기준점
    prev_game_time = await snapshot_game_time()
    recent_story   = "\n".join(recent_responses[-RECENT_STORY_TURNS:])

    # 2. Manager: 씬 분류 + 시간 계산 + 프롬프트 조립
    async with cl.Step(name="데이터 추출", show_input=False) as step:
        fixed, genre, dynamic, scene_types, manager_effects = await run_manager(
            user_input   = user_input,
            pc_id        = pc_id,
            npc_id       = npc_id,
            recent_story = recent_story,
            world_id     = world_id,
            perspective  = PERSPECTIVE,
            return_meta  = True,
            suppress_time_plan=bool(ooc_result and ooc_result.get("time_changed")),
            scene_need_hints=cl.user_session.get("scene_need_hints") or {},
        )
        suffix      = f" {step_suffix}" if step_suffix else ""
        step.output = f"씬 타입: `{scene_types}`{suffix}"

    if ooc_result and ooc_result.get("time_changed"):
        manager_effects["ooc_time_patch"] = ooc_result
        manager_effects["time_plan"] = None
        manager_effects["ooc_time_after"] = ooc_result.get("time_after")
        manager_effects["needs_update"] = {
            "pc_id": pc_id,
            "elapsed_minutes": float(ooc_result.get("elapsed_minutes") or 0.0),
            "current_time": ooc_result.get("time_after"),
        }
        manager_effects["daily_systems"] = {
            "days_passed": int(ooc_result.get("days_passed") or 0),
            "current_time": ooc_result.get("time_after"),
        }
        manager_effects["pending_effects"] = [
            effect for effect in manager_effects.get("pending_effects", [])
            if effect.get("type") not in {"global_time_update", "global_weather_update", "location_update"}
        ]

    # 유저노트를 매 턴마다 파일에서 직접 읽어 dynamic 프롬프트 최상단에 삽입
    thread_id = cl.context.session.thread_id
    note_block = build_usernote_block(load_usernote(thread_id))
    if note_block:
        dynamic = note_block + dynamic

    debug_dir = write_turn_debug_snapshot(
        user_input      = user_input,
        fixed_prompt    = fixed,
        genre_prompt    = genre,
        dynamic_prompt  = dynamic,
        scene_types     = scene_types,
        manager_effects = manager_effects,
        history         = history,
        world_id        = world_id,
        pc_id           = pc_id,
        npc_id          = npc_id,
        npc_name        = npc_name_kor,
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
        npc_name       = npc_name_kor,
        logs_dir       = _LOGS_DIR,
        status_text    = random.choice(GENERATING_MSGS).format(char=npc_name_kor),
    )
    if hour is None:
        hour = hour_from_time_string(await snapshot_game_time())

    # 4. 첫 번째 RP 턴이면 스레드 이름을 [날짜]-[세계관] 형식으로 설정
    if not history and not step_suffix:
        try:
            thread_id = cl.context.session.thread_id
            name = f"{datetime.now().strftime('%Y-%m-%d')}-{world_id}"
            await cl.data_layer.update_thread(thread_id=thread_id, name=name)
        except Exception:
            pass

    # 5. 히스토리 갱신 — 리롤을 위한 스냅샷을 먼저 보존
    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    # msg_id를 함께 저장해 삭제 액션이 history 항목을 찾을 수 있게 한다
    history += [
        {"role": "user",      "content": user_input,    "msg_id": user_msg_id},
        {"role": "assistant", "content": full_response, "msg_id": response_msg.id},
    ]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])

    narrative_turns: list[dict] = cl.user_session.get("narrative_turns") or []
    narrative_turns.append({"user": user_input, "actor": full_response})
    if len(narrative_turns) >= 10:
        asyncio.create_task(_compress_narrative(list(narrative_turns), npc_id, pc_id))
        narrative_turns = []
    cl.user_session.set("narrative_turns", narrative_turns)

    # 6. 다음 턴에서 확정될 pending 등록
    # scene_chars에는 Actor 사고에서 추출한 한국어 이름과 manager가 감지한 NPC ID를 함께 넣는다.
    # run_needs_update는 npc_id(영문)로 scene_set을 조회하므로 ID가 반드시 포함되어야 한다.
    scene_chars = list(set(scene_chars or []) | set(manager_effects.get("scene_npc_ids") or []))
    pending_commit = PendingCommit(
        user_input=user_input,
        ai_response=full_response,
        scene_types=scene_types,
        scene_chars=scene_chars,
        timestamp=datetime.now(),
        history_snapshot=history_snapshot,
        recent_snapshot=recent_snapshot,
        prev_game_time=prev_game_time,
        manager_effects=manager_effects,
        ooc_result=ooc_result,
        time_plan=manager_effects.get("time_plan"),
        pending_effects=manager_effects.get("pending_effects", []),
        debug_dir=debug_dir,
        user_msg_id=user_msg_id or cl.user_session.get("reroll_user_msg_id"),
        response_msg_id=response_msg.id,
    )
    cl.user_session.set("pending_commit", pending_commit.model_dump())
    cl.user_session.set("reroll_user_msg_id", None)

    response_msg.actions = make_actions()
    await response_msg.update()
    await inject_time_theme(hour, for_id=response_msg.id)


# ════════════════════════════════════════════════════════════
# 리롤
# ════════════════════════════════════════════════════════════

@cl.action_callback("reroll")
async def on_reroll(action: cl.Action) -> None:
    """이전 응답 메시지를 삭제하고 같은 입력으로 재생성한다."""
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
    _, _, _, npc_name_kor, _ = _wv()
    await show_edit_form(msg_id, pending.get("ai_response", ""), npc_name_kor)


# ════════════════════════════════════════════════════════════
# 메시지 삭제
# ════════════════════════════════════════════════════════════

@cl.action_callback("delete_message")
async def on_delete_message(action: cl.Action) -> None:
    """
    메시지를 UI와 대화 히스토리에서 제거합니다.

    삭제된 메시지와 연관된 pending_commit이 있으면 함께 취소합니다.
    DB 일관성은 사용자 책임이며 별도 롤백은 수행하지 않습니다.
    """
    msg_id = action.forId
    if not msg_id:
        return

    # UI에서 제거 — Chainlit이 내부적으로 data_layer.delete_step()을 호출
    try:
        await cl.Message(id=msg_id, content="").remove()
    except Exception:
        pass

    # conversation_history에서 해당 항목 제거
    history: list[dict] = cl.user_session.get("conversation_history") or []
    history = [h for h in history if h.get("msg_id") != msg_id]
    cl.user_session.set("conversation_history", history)

    # 삭제된 메시지가 pending_commit과 연관되면 pending을 취소
    pending = cl.user_session.get("pending_commit")
    if pending and (
        pending.get("response_msg_id") == msg_id
        or pending.get("user_msg_id") == msg_id
    ):
        cl.user_session.set("pending_commit", None)


# ════════════════════════════════════════════════════════════
# 세션 초기화 / 재개 / 종료
# ════════════════════════════════════════════════════════════

async def _remove_legacy_graph_steps(steps: list[dict]) -> None:
    """이전 GraphDebug 메시지가 채팅 기록에 남아 있으면 UI에서 제거합니다."""
    for step in steps:
        output = step.get("output") or ""
        if not (
            output.startswith("**Live Graph**")
            or output.startswith("**Debug Graph**")
            or output.startswith("그래프 관찰 창:")
        ):
            continue
        step_id = step.get("id")
        if not step_id:
            continue
        try:
            await cl.Message(id=step_id, content="").remove()
        except Exception:
            pass


@cl.on_chat_start
async def on_chat_start() -> None:
    """신규 채팅방 세션 변수를 초기화하고 오프닝 씬을 출력합니다."""
    # ChatProfile 이름에서 세계관 ID와 시나리오 ID를 파싱합니다.
    # 형식: 'rofan/academy' → ('rofan', 'academy'), 'babe_univ' → ('babe_univ', None)
    profile_name          = cl.user_session.get("chat_profile") or WORLD_ID
    world_id, scenario_id = _parse_profile_name(profile_name)
    thread_id             = cl.context.session.thread_id

    await _init_session_world(world_id, thread_id, scenario_id, create_driver=False)

    world_config  = cl.user_session.get("world_config") or {}
    opening_scene = (
        world_config.get("opening_scene")
        or world_config.get("prompt", {}).get("sections", {}).get("opening_scene", "")
    )

    cl.user_session.set("conversation_history", [])
    cl.user_session.set("recent_responses",     [opening_scene] if opening_scene else [])
    cl.user_session.set("narrative_turns",      [])
    cl.user_session.set("pending_commit",        None)
    cl.user_session.set("last_theme_hour",       -1)
    cl.user_session.set("pending_ooc",            None)
    cl.user_session.set("debug_graph_msg_id",     None)
    cl.user_session.set("traits_initialized",     False)

    await cl.Message(
        content=(
            "**GraphRAG 롤플레이 시작**\n\n"
            "- 일반 입력: 롤플레이 진행\n"
            "- `*` 로 시작: OOC 명령 (예: `*3시간 후.`, `*장소: 헬스장`)\n"
            "- 왼쪽 사이드바: 채팅방 목록\n---"
        )
    ).send()
    if opening_scene:
        await cl.Message(content=opening_scene, author="세계").send()
    await inject_time_theme(hour_from_time_string(await snapshot_game_time()))


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """기존 채팅방을 재개할 때 세션 변수를 스레드 데이터로 복원합니다."""
    metadata    = thread.get("metadata") or {}
    world_id    = metadata.get("world_id")    or WORLD_ID
    scenario_id = metadata.get("scenario_id")
    thread_id   = thread["id"]

    await _init_session_world(world_id, thread_id, scenario_id)

    steps = thread.get("steps") or []
    await _remove_legacy_graph_steps(steps)
    history, recents = build_history_from_steps(
        steps,
        max_history_turns=MAX_HISTORY_TURNS,
        recent_story_turns=RECENT_STORY_TURNS,
    )

    narrative_turns = []
    for i in range(0, len(history) - 1, 2):
        u, a = history[i], history[i + 1]
        if u["role"] == "user" and a["role"] == "assistant":
            narrative_turns.append({"user": u["content"], "actor": a["content"]})
    narrative_turns = narrative_turns[-10:]

    cl.user_session.set("conversation_history", history)
    cl.user_session.set("recent_responses",     recents)
    cl.user_session.set("narrative_turns",      narrative_turns)
    cl.user_session.set("pending_commit",        None)
    cl.user_session.set("last_theme_hour",       -1)
    cl.user_session.set("pending_ooc",            None)
    cl.user_session.set("debug_graph_msg_id",     None)
    cl.user_session.set("traits_initialized",     False)

    await _ensure_session_traits_initialized()
    await inject_time_theme(hour_from_time_string(await snapshot_game_time()))
    await upsert_debug_graph(pc_id=cl.user_session.get("pc_id"), npc_id=cl.user_session.get("npc_id"), world_id=world_id)


@cl.on_chat_end
async def on_chat_end() -> None:
    """채팅 종료 시 미확정 pending을 세션 종료 전에 강제 처리합니다."""
    db_path = cl.user_session.get("db_path")
    if db_path:
        stale = _ACTIVE_DRIVERS.pop(db_path, None)
        if stale is None:
            stale = cl.user_session.get("db_driver")
        if stale is not None:
            stale.close()

    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
    world_id, pc_id, npc_id, _, world_config = _wv()
    await commit_pending(
        pending=pending,
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        world_config=world_config,
        scheduler=_get_scheduler(world_id),
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
    2. 수정/리롤/empty/system 명령은 즉시 처리 후 종료
    3. 확정 가능한 이전 pending을 커밋
    4. OOC-only와 설정 QA는 Actor 파이프라인을 건너뜀
    5. RP 입력만 Manager → Actor → 히스토리 갱신 (_run_generation)
    """
    await _ensure_db_driver()

    world_id, pc_id, npc_id, npc_name_kor, world_config = _wv()

    user_input = message.content.strip()
    route = route_user_input(user_input, message)

    # ── 수정 완료·취소 감지 — CustomElement에서 보내는 특수 접두사 ──
    if route == TurnInputType.EDIT and user_input.startswith("__EDIT__:"):
        await message.remove()
        await apply_edit(user_input[9:], npc_name_kor)
        return

    if route == TurnInputType.EDIT and user_input == "__EDIT_CANCEL__":
        await message.remove()
        await cancel_edit(npc_name_kor)
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
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        world_config=world_config,
        updating_msgs=UPDATING_MSGS,
        updated_msgs=UPDATED_MSGS,
        scheduler=_get_scheduler(world_id),
    )
    await upsert_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)

    if route == TurnInputType.EMPTY or not user_input:
        return

    if route == TurnInputType.SYSTEM_COMMAND:
        await _handle_system_command(user_input)
        return

    history          = cl.user_session.get("conversation_history")
    recent_responses = cl.user_session.get("recent_responses")

    # ── 임신 OOC 자동 주입 ───────────────────────────────────
    pending_ooc = cl.user_session.get("pending_ooc")
    if pending_ooc:
        cl.user_session.set("pending_ooc", None)
        user_input = f"{pending_ooc}\n{user_input}"
        await cl.Message(content=pending_ooc, author="시스템").send()
        route = route_user_input(user_input, message)

    # ── OOC 처리 ─────────────────────────────────────────────
    ooc_result: dict | None = None
    if is_ooc(user_input):
        ooc_changes: dict = {}
        async with cl.Step(name="⚙️ OOC", show_input=False) as step:
            result      = await parse_ooc(user_input, npc_id, npc_name_kor, pc_id=pc_id)
            ooc_result  = result
            ooc_changes = result.get("state_changes", {})
            lines       = [f"**{result['summary']}**"]
            for char_name, char_state in ooc_changes.items():
                for k, v in char_state.items():
                    lines.append(f"- `{char_name}.{k}` -> `{v}`")
            if result.get("time_changed"):
                lines += [
                    f"- `time_before` -> `{result.get('time_before')}`",
                    f"- `time_after` -> `{result.get('time_after')}`",
                    f"- `elapsed_minutes` -> `{result.get('elapsed_minutes')}`",
                    f"- `days_passed` -> `{result.get('days_passed')}`",
                    f"- `applied_time_delta_minutes` -> `{result.get('applied_time_delta_minutes')}`",
                    f"- `applied_time_set` -> `{result.get('applied_time_set')}`",
                ]
            if result.get("location_id"):
                lines.append(f"- `location_id` -> `{result.get('location_id')}`")
                lines.append(f"- `moved_character_ids` -> `{result.get('moved_character_ids')}`")
            step.output = "\n".join(lines)

        npc_state_changes = ooc_changes.get(npc_name_kor, {})
        if npc_state_changes.get("physical_condition") in ("injured", "ill", "hospitalized"):
            await delegate_complex_update(user_input, npc_id, pc_id, npc_state_changes, event_only=True)
        ooc_participant_ids = [pc_id, npc_id]
        for moved_char_id in result.get("moved_character_ids") or []:
            if moved_char_id and moved_char_id not in ooc_participant_ids:
                ooc_participant_ids.append(moved_char_id)
        named_updates = await apply_multi_character_state_updates(
            user_input,
            pc_id,
            participant_ids=ooc_participant_ids,
        )
        if named_updates:
            async with cl.Step(name="보조 캐릭터 상태", show_input=False) as step:
                step.output = "\n".join(
                    f"- `{item['char_id']}` -> `{item['dynamic_state']}`"
                    for item in named_updates
                )
        await _sses_advance_slot_if_needed(world_id, ooc_changes)
        await upsert_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)

        if route == TurnInputType.OOC_PATCH:
            # *마커* 를 벗겨야 actor가 '도'(also) 를 이전 context 인물과 연관짓지 않는다.
            user_input = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", user_input, flags=re.DOTALL).strip()
            if not user_input:
                return
            route = TurnInputType.ROLEPLAY

    # ── RP 턴: 유저 메시지에 삭제 버튼 추가 ─────────────────
    message.actions = [
        cl.Action(name="delete_message", label="🗑️", payload={"action": "delete"})
    ]
    await message.update()

    # ── 생성 파이프라인 ───────────────────────────────────────
    await _run_generation(
        user_input,
        history,
        recent_responses,
        ooc_result=ooc_result,
        user_msg_id=getattr(message, "id", None),
    )
    await upsert_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
