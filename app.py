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
#   - _upsert_debug_graph_best_effort(pc_id: str | None, npc_id: str | None, world_id: str) -> bool : 디버그 그래프 갱신
#   - _init_session_world(world_id: str, thread_id: str, scenario_id: str | None = None, *, create_driver: bool = True) -> None : 세션 월드 상태 초기화
#   - _ensure_db_driver() -> None : 지연 생성된 세션 Kuzu 드라이버 보장
#   - _current_perspective() -> int : 현재 세션의 월드 기본 시점 반환
#   - _current_game_datetime() -> datetime : 현재 인게임 시간을 datetime으로 반환
#   - _social_media_features() -> dict : 현재 월드/세션 기준 카카오톡·인스타그램 활성 상태 반환
#   - _queue_kakao_message(pc_id: str, room_id: str, content: str) -> None : 이번 턴 카카오톡 전송 버퍼에 메시지 추가
#   - _repair_guarded_actor_output(full_response: str, blocked_terms: list[str]) -> str | None : 출력 금지어 위반 응답 수정
#   - _handle_kakao_panel_event(raw_payload: str) -> None : 카카오톡 패널 이벤트 처리
#   - _handle_social_panel_event(raw_payload: str) -> None : SNS 패널 설정 이벤트 처리
#   - on_chat_start() -> None : 신규 세션 초기화 및 오프닝 씬 출력
#   - on_chat_resume(thread: ThreadDict) -> None : 기존 채팅방 재개 시 세션 복원
#   - _remove_legacy_graph_steps(steps: list[dict]) -> None : 이전 그래프 메시지 제거
#   - on_chat_end() -> None : 미확정 pending 강제 처리
#   - on_message(message: cl.Message) -> None : 메시지 루프 메인 핸들러
#   - _handle_system_command(user_input: str) -> None : help/debug graph 명령 처리
#   - _find_user_history_index(history: list[dict], msg_id: str | None) -> int | None : 사용자 메시지 history 위치 검색
#   - _chat_context_message_ids() -> set[str] : Chainlit 현재 chat context의 메시지 ID 집합 반환
#   - _first_removed_assistant_index(history: list[dict], active_ids: set[str]) -> int | None : UI에서 제거된 assistant 위치 검색
#   - _recent_responses_from_history(history: list[dict]) -> list[str] : history에서 최근 응답 스냅샷 재구성
#   - _handle_user_message_edit(message: cl.Message, user_input: str) -> bool : Chainlit 기본 사용자 메시지 수정 처리
#   - on_reroll(action: cl.Action) -> None : 리롤 버튼 콜백
#   - on_edit_response(action: cl.Action) -> None : 수정 버튼 콜백
#   - on_delete_message(action: cl.Action) -> None : 삭제 버튼 콜백
# ================================

import asyncio
import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

import chainlit as cl
from chainlit.chat_context import chat_context
from chainlit.types import ThreadDict

from src.config import WORLD_ID, MODEL_ACTOR, MODEL_OUTPUT_REPAIR, MAX_TOKEN
from src.agents.manager import run_manager
from src.agents.prompt_factory.ooc_handler import is_ooc, parse_ooc
from src.agents.prompt_factory.usernote import build_usernote_block, load_usernote
from src.ui.history import build_history_from_steps
from src.ui.input_routing import TurnInputType, route_user_input
from src.ui.kakao_panel import send_kakao_panel
from src.ui.output_guard import find_forbidden_terms, find_pov_violations
from src.ui.output_repair import repair_actor_output
from src.ui.pending_store import discard_pending_commit, save_pending_commit
from src.ui.social_media_settings import (
    resolve_social_media_features,
    set_social_media_override,
)
from src.ui.session_models import PendingCommit
from src.ui.session_world import (
    close_session_driver as _close_session_driver,
    discover_world_profiles as _discover_world_profiles,
    ensure_db_driver as _ensure_session_db_driver,
    init_session_world as _init_session_world_state,
    is_closed_connection_error as _is_closed_connection_error,
    parse_profile_name as _parse_profile_name,
    resolve_thread_world as _resolve_thread_world,
)
from src.ui.turn_debug import write_turn_debug_snapshot
from src.ui.actor_stream import stream_actor
from src.ui.deferred_commit import commit_pending
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
from src.simulation.systems.organic import set_pregnant_manual
from src.simulation.systems.kakao import (
    invite_character,
)
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


async def _upsert_debug_graph_best_effort(pc_id: str | None, npc_id: str | None, world_id: str) -> bool:
    """디버그 그래프를 갱신하고, 종료 중 닫힌 연결이면 실패를 흡수합니다."""
    if not pc_id or not npc_id:
        return False
    try:
        await upsert_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
        return True
    except Exception as exc:
        if _is_closed_connection_error(exc):
            logging.getLogger(__name__).debug("Skipped debug graph update after connection close: %s", exc)
            return False
        raise


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

# ── UI 표시 마커 ────────────────────────────────────────────────
# Chainlit 내부 DOM 클래스는 버전마다 바뀌므로, CSS 선택자만으로 메시지 박스를
# 안정적으로 잡기 어렵습니다. 대신 화면에 보이지 않는 유니코드 마커를 메시지 앞뒤에
# 심고, custom.js가 이 마커의 공통 부모를 정확히 찾아 테두리를 적용합니다.
_UI_MARKERS = {
    "actor": ("\u2060\u2061\u2062\u2063", "\u2063\u2062\u2061\u2060"),
    "user":  ("\u2060\u2060\u2061\u2061", "\u2062\u2062\u2063\u2063"),
}


def _mark_ui_message(content: str, kind: str) -> str:
    """채팅 화면에서 custom.js가 메시지 박스를 찾도록 본문 양끝에 보이지 않는 마커를 붙입니다."""
    start, end = _UI_MARKERS.get(kind, _UI_MARKERS["actor"])
    return f"{start}{content}{end}"


def _strip_ui_markers(content: str | None) -> str:
    """히스토리/라우팅/편집 처리에 UI 마커가 섞이지 않도록 제거합니다."""
    if not content:
        return ""
    text = str(content)
    for start, end in _UI_MARKERS.values():
        text = text.replace(start, "").replace(end, "")
    return text.strip()


async def _ui_toast(message: str, type_: str = "info", *, duration: float = 1.35) -> None:
    """중간 처리 상태를 채팅 로그에 남기지 않고 짧게 표시합니다."""
    try:
        await cl.context.emitter.send_toast(message, type_)
        return
    except Exception:
        pass

    temp = cl.Message(content=f"⏳ {message}", author="시스템")
    try:
        await temp.send()
        await asyncio.sleep(duration)
        await temp.remove()
    except Exception:
        try:
            await temp.remove()
        except Exception:
            pass


async def _commit_pending_if_any_quiet(
    *,
    world_id: str,
    pc_id: str,
    npc_id: str,
    world_config: dict,
    scheduler=None,
) -> None:
    """이전 턴 pending commit을 채팅 메시지 없이 처리합니다."""
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return

    await _ui_toast(random.choice(UPDATING_MSGS), "info")
    try:
        await commit_pending(
            pending=pending,
            world_id=world_id,
            pc_id=pc_id,
            npc_id=npc_id,
            world_config=world_config,
            scheduler=scheduler,
            show_toast=False,
        )
        cl.user_session.set("pending_commit", None)
        discard_pending_commit(pending, world_id, pc_id, npc_id)
        await _ui_toast(random.choice(UPDATED_MSGS), "success")
    except Exception:
        await _ui_toast("세계 상태를 갱신하는 중 문제가 생겼습니다.", "error")
        raise


def _thread_sidebar_name(world_id: str, scenario_id: str | None, world_config: dict, last_message: str) -> str:
    """사이드바 채팅방 제목을 '세계관/시나리오 > 마지막 메시지' 형식으로 만듭니다."""
    world_label = (
        world_config.get("display_name")
        or world_config.get("name")
        or world_id
    )
    scenario_label = (
        world_config.get("scenario_name")
        or scenario_id
        or "default"
    )
    preview = re.sub(r"\s+", " ", last_message).strip()
    preview = preview.replace("**", "")
    if len(preview) > 72:
        preview = preview[:69] + "..."
    return f"{world_label}/{scenario_label}\n> {preview}"


async def _update_thread_sidebar_name(last_message: str) -> None:
    """Actor 응답 기준으로 사이드바 채팅방 표시명을 갱신합니다."""
    world_id, _, _, _, world_config = _wv()
    scenario_id = cl.user_session.get("scenario_id")
    thread_id = cl.context.session.thread_id
    title = _thread_sidebar_name(world_id, scenario_id, world_config, last_message)
    tag = f"{world_id}/{scenario_id}" if scenario_id else world_id
    try:
        await cl.data_layer.update_thread(
            thread_id=thread_id,
            name=title,
            metadata={"world_id": world_id, "scenario_id": scenario_id},
            tags=[tag],
        )
    except Exception:
        pass


MAX_HISTORY_TURNS  = 10
RECENT_STORY_TURNS = 3
_LOGS_DIR          = Path("logs")
_TURN_DEBUG_DIR    = _LOGS_DIR / "turn_debug"

_genai_client = get_client()


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
    """세션 변수에 월드 설정과 per-thread Kuzu 드라이버를 설정합니다."""
    await _init_session_world_state(
        world_id=world_id,
        thread_id=thread_id,
        scenario_id=scenario_id,
        create_driver=create_driver,
    )


async def _ensure_db_driver() -> None:
    """db_driver가 없으면 지금 생성합니다 (신규 채팅 첫 메시지 진입 시 호출)."""
    await _ensure_session_db_driver(default_world_id=WORLD_ID)
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


def _current_perspective() -> int:
    """현재 세션의 월드 기본 시점을 반환합니다."""
    world_config = cl.user_session.get("world_config") or {}
    return int(cl.user_session.get("perspective") or world_config.get("perspective") or 3)


async def _current_game_datetime() -> datetime:
    """현재 인게임 시간을 datetime으로 반환하고 실패 시 실제 현재 시각을 반환합니다."""
    raw_time = await snapshot_game_time()
    if raw_time:
        try:
            return datetime.fromisoformat(raw_time)
        except ValueError:
            pass
    return datetime.now()


def _social_media_features() -> dict:
    """현재 월드 강제 설정과 세션 override를 합쳐 SNS 기능 상태를 반환합니다."""
    return resolve_social_media_features(
        cl.user_session.get("world_config") or {},
        cl.user_session.get("social_media_overrides") or {},
    )


async def _queue_kakao_message(pc_id: str, room_id: str, content: str) -> None:
    """이번 Actor 턴 전에 확정할 플레이어 카카오톡 메시지를 세션 버퍼에 추가합니다."""
    if not pc_id or not room_id or not content.strip():
        return
    pending = cl.user_session.get("pending_kakao_messages") or []
    pending.append({
        "room_id": room_id,
        "sender_id": pc_id,
        "content": content.strip(),
        "created_at": (await _current_game_datetime()).isoformat(),
    })
    cl.user_session.set("pending_kakao_messages", pending)


async def _repair_guarded_actor_output(full_response: str, blocked_terms: list[str]) -> str | None:
    """Guard 위반 Actor 응답을 Pro repair로 수정하고 통과한 본문만 반환합니다."""
    print(f"[OutputGuard] repairing Actor output with {MODEL_OUTPUT_REPAIR}: {blocked_terms}")
    repaired = await repair_actor_output(
        actor_output=full_response,
        blocked_terms=blocked_terms,
        model_name=MODEL_OUTPUT_REPAIR,
    )
    repaired_terms = find_forbidden_terms(repaired)
    if repaired_terms:
        print(f"[OutputGuard] repair rejected for forbidden terms: {repaired_terms}")
        await cl.Message(
            content=(
                "Actor 출력 수정본도 blacklist를 위반해서 이번 응답을 커밋하지 않았습니다.\n"
                f"감지된 표현: `{', '.join(repaired_terms[:12])}`"
            ),
            author="시스템",
        ).send()
        return None
    print("[OutputGuard] repair accepted")
    return repaired


async def _handle_kakao_panel_event(raw_payload: str) -> None:
    """카카오톡 패널에서 보낸 큐잉/초대/선택 이벤트를 처리합니다."""
    _, pc_id, npc_id, _, _ = _wv()
    features = _social_media_features()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return

    action = payload.get("action")
    room_id = str(payload.get("roomId") or "")
    if not features["kakao_enabled"]:
        await send_kakao_panel(
            pc_id,
            npc_id,
            active_room_id=room_id,
            current_time=await _current_game_datetime(),
            features=features,
        )
        return
    if action == "select_room":
        await send_kakao_panel(
            pc_id,
            npc_id,
            active_room_id=room_id,
            current_time=await _current_game_datetime(),
            features=features,
        )
        return
    if action == "queue_message":
        content = str(payload.get("content") or "").strip()
        if content:
            await _queue_kakao_message(pc_id, room_id, content)
        await send_kakao_panel(
            pc_id,
            npc_id,
            active_room_id=room_id,
            current_time=await _current_game_datetime(),
            features=features,
        )
        return
    if action == "invite":
        char_id = str(payload.get("charId") or "").strip()
        if char_id:
            await invite_character(
                pc_id=pc_id,
                room_ref=room_id,
                char_ref=char_id,
                current_time=await _current_game_datetime(),
            )
        await send_kakao_panel(
            pc_id,
            npc_id,
            active_room_id=room_id,
            current_time=await _current_game_datetime(),
            features=features,
        )


async def _handle_social_panel_event(raw_payload: str) -> None:
    """SNS 패널에서 보낸 활성화/비활성화 설정 이벤트를 처리합니다."""
    _, pc_id, npc_id, _, world_config = _wv()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return

    if payload.get("action") == "set_feature":
        overrides = set_social_media_override(
            feature=str(payload.get("feature") or ""),
            enabled=bool(payload.get("enabled")),
            world_config=world_config,
            overrides=cl.user_session.get("social_media_overrides") or {},
        )
        cl.user_session.set("social_media_overrides", overrides)
        if not _social_media_features()["kakao_enabled"]:
            cl.user_session.set("pending_kakao_messages", [])
    await send_kakao_panel(
        pc_id,
        npc_id,
        current_time=await _current_game_datetime(),
        features=_social_media_features(),
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
                "- `/임신 [이름]`: 캐릭터 임신 상태 수동 설정 (자동 감지 실패 시 보정용)\n"
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
    if user_input.strip().split()[0].lower() in {"/임신", "!임신"}:
        parts = user_input.strip().split(maxsplit=1)
        if len(parts) < 2:
            await cl.Message(content="사용법: `/임신 [캐릭터 이름 또는 ID]`", author="시스템").send()
            return
        char_ref = parts[1].strip()
        result = await set_pregnant_manual(char_ref)
        if result is None:
            await cl.Message(
                content=f"캐릭터를 찾을 수 없습니다: `{char_ref}`\n이름 또는 ID를 확인하세요.",
                author="시스템",
            ).send()
        else:
            await cl.Message(content=result, author="시스템").send()
        return
    await cl.Message(content=f"알 수 없는 시스템 명령입니다: `{user_input}`", author="시스템").send()


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
    step_suffix는 리롤/수정 여부를 내부 처리에만 사용한다.
    Actor 응답을 기준으로 사이드바 채팅방 표시명을 갱신한다.
    """
    world_id, pc_id, npc_id, npc_name_kor, _ = _wv()
    commit_id = uuid4().hex
    thread_id = cl.context.session.thread_id

    # 1. 현재 인게임 시간 스냅샷 — 리롤 시 복원 기준점
    prev_game_time = await snapshot_game_time()
    recent_story   = "\n".join(recent_responses[-RECENT_STORY_TURNS:])
    queued_kakao_messages = cl.user_session.get("pending_kakao_messages") or []

    # 2. Manager: 씬 분류 + 시간 계산 + 프롬프트 조립
    await _ui_toast("데이터를 수집하고 장면을 정리하는 중입니다.", "info")
    fixed, genre, dynamic, scene_types, manager_effects = await run_manager(
        user_input   = user_input,
        pc_id        = pc_id,
        npc_id       = npc_id,
        recent_story = recent_story,
        world_id     = world_id,
        scenario_id  = cl.user_session.get("scenario_id"),
        perspective  = _current_perspective(),
        return_meta  = True,
        suppress_time_plan=bool(ooc_result and ooc_result.get("time_changed")),
        scene_need_hints=cl.user_session.get("scene_need_hints") or {},
        pending_kakao_messages=queued_kakao_messages,
        enable_kakao_preprocessing=not bool(step_suffix) and _social_media_features()["kakao_enabled"],
        social_media_features=_social_media_features(),
        thread_id=thread_id,
        commit_id=None if ooc_result else commit_id,
    )
    if manager_effects.get("kakao_panel_refresh"):
        await send_kakao_panel(
            pc_id,
            npc_id,
            current_time=await _current_game_datetime(),
            features=_social_media_features(),
        )

    if ooc_result:
        manager_effects["ooc_patch_result"] = ooc_result
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
    note_block = build_usernote_block(load_usernote(thread_id))
    if note_block:
        dynamic = note_block + dynamic

    # 이전 턴 CoT를 dynamic 끝에 주입 (모델이 이전 계산 결과를 참조할 수 있도록)
    prev_cot = cl.user_session.get("prev_cot") or ""
    if prev_cot:
        dynamic = dynamic + f"\n\n[Previous Turn CoT]\n{prev_cot}"

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

    # 5a. 스트리밍 전에 스냅샷 보존 — await 중 다른 코루틴이 같은 list를 mutate할 수 있다
    history_snapshot = list(history)
    recent_snapshot  = list(recent_responses)

    # 3. Actor 스트리밍
    # blacklist는 프롬프트 지시만으로는 강제되지 않으므로, pending/history 등록 전 검사한다.
    full_response, scene_chars, response_msg, hour, raw_thinking = await stream_actor(
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
        send_output    = False,
    )
    blocked_terms = find_forbidden_terms(full_response) + find_pov_violations(full_response, _current_perspective())
    if blocked_terms:
        print(f"[OutputGuard] rejected Actor output for forbidden terms: {blocked_terms}")
        repaired_response = await _repair_guarded_actor_output(full_response, blocked_terms)
        if repaired_response is None:
            return
        full_response = repaired_response

    response_msg.content = _mark_ui_message(full_response, "actor")
    await response_msg.send()
    await _update_thread_sidebar_name(full_response)
    if hour is None:
        hour = hour_from_time_string(await snapshot_game_time())

    # 5. 히스토리 갱신
    # msg_id를 함께 저장해 삭제 액션이 history 항목을 찾을 수 있게 한다
    effective_user_msg_id = user_msg_id or cl.user_session.get("reroll_user_msg_id")
    history += [
        {"role": "user",      "content": user_input,    "msg_id": effective_user_msg_id},
        {"role": "assistant", "content": full_response, "msg_id": response_msg.id},
    ]
    del history[:-MAX_HISTORY_TURNS * 2]
    cl.user_session.set("conversation_history", history)

    recent_responses.append(full_response[:1500])
    cl.user_session.set("recent_responses", recent_responses[-RECENT_STORY_TURNS:])

    # 6. 다음 턴에서 확정될 pending 등록
    # scene_chars에는 Actor 사고에서 추출한 한국어 이름과 manager가 감지한 NPC ID를 함께 넣는다.
    # run_needs_update는 npc_id(영문)로 scene_set을 조회하므로 ID가 반드시 포함되어야 한다.
    scene_chars = list(set(scene_chars or []) | set(manager_effects.get("scene_npc_ids") or []))
    pending_commit = PendingCommit(
        commit_id=commit_id,
        thread_id=cl.context.session.thread_id,
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
        pending_kakao_messages=queued_kakao_messages if manager_effects.get("kakao_processed") else [],
        pending_effects=manager_effects.get("pending_effects", []),
        debug_dir=debug_dir,
        user_msg_id=effective_user_msg_id,
        response_msg_id=response_msg.id,
        prev_cot=cl.user_session.get("prev_cot") or "",
    )
    pending_dict = pending_commit.model_dump(mode="json")
    cl.user_session.set("pending_commit", pending_dict)
    save_pending_commit(pending_dict, world_id, pc_id, npc_id)
    if manager_effects.get("kakao_processed"):
        cl.user_session.set("pending_kakao_messages", [])
    cl.user_session.set("prev_cot",       raw_thinking)
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
    world_id: str | None = None,
    pc_id: str | None = None,
    npc_id: str | None = None,
) -> None:
    """현재 pending 응답을 버리고 같은 사용자 입력으로 다시 생성합니다."""
    session_world_id, session_pc_id, session_npc_id, _, _ = _wv()
    world_id = world_id or session_world_id
    pc_id = pc_id or session_pc_id
    npc_id = npc_id or session_npc_id
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
    discard_pending_commit(pending, world_id, pc_id, npc_id)
    cl.user_session.set("prev_cot",              pending.get("prev_cot", ""))
    if pending.get("pending_kakao_messages"):
        cl.user_session.set("pending_kakao_messages", pending.get("pending_kakao_messages") or [])

    if pending.get("prev_game_time"):
        await restore_game_time(pending["prev_game_time"])

    if replacement_user_input is not None:
        pending["user_input"] = replacement_user_input

    cl.user_session.set("reroll_user_msg_id", pending.get("user_msg_id"))

    history          = cl.user_session.get("conversation_history")
    recent_responses = cl.user_session.get("recent_responses")
    await _run_generation(pending["user_input"], history, recent_responses, step_suffix="(리롤)")


def _find_user_history_index(history: list[dict], msg_id: str | None) -> int | None:
    """history에서 msg_id가 일치하는 사용자 메시지 위치를 반환합니다."""
    if not msg_id:
        return None
    for idx, item in enumerate(history):
        if item.get("role") == "user" and item.get("msg_id") == msg_id:
            return idx
    return None


def _chat_context_message_ids() -> set[str]:
    """현재 Chainlit chat context에 남아 있는 메시지 ID 집합을 반환합니다."""
    try:
        return {
            msg_id
            for msg_id in (getattr(item, "id", None) for item in chat_context.get())
            if msg_id
        }
    except Exception:
        return set()


def _first_removed_assistant_index(history: list[dict], active_ids: set[str]) -> int | None:
    """history에는 있으나 현재 UI context에서 제거된 첫 assistant 위치를 반환합니다."""
    if not active_ids:
        return None
    for idx, item in enumerate(history):
        if (
            item.get("role") == "assistant"
            and item.get("msg_id")
            and item.get("msg_id") not in active_ids
        ):
            return idx
    return None


def _recent_responses_from_history(history: list[dict]) -> list[str]:
    """history의 assistant 항목으로 recent_responses 값을 재구성합니다."""
    return [
        _strip_ui_markers(item.get("content", ""))[:1500]
        for item in history
        if item.get("role") == "assistant" and item.get("content")
    ][-RECENT_STORY_TURNS:]


async def _handle_user_message_edit(message: cl.Message, user_input: str) -> bool:
    """Chainlit 기본 사용자 메시지 편집 Confirm을 처리했으면 True를 반환합니다."""
    msg_id = getattr(message, "id", None)
    history: list[dict] = cl.user_session.get("conversation_history") or []
    active_ids = _chat_context_message_ids()
    pending = cl.user_session.get("pending_commit")

    edit_idx = _find_user_history_index(history, msg_id)
    if edit_idx is None:
        removed_idx = _first_removed_assistant_index(history, active_ids)
        if removed_idx is None:
            return False
        edit_idx = removed_idx - 1

    if edit_idx < 0 or history[edit_idx].get("role") != "user":
        return False

    edited_existing_turn = (
        user_input != history[edit_idx].get("content")
        or edit_idx < len(history) - 1
    )
    if not edited_existing_turn:
        return False

    if pending and pending.get("user_msg_id") == msg_id:
        await _reroll_pending_response(replacement_user_input=user_input)
        return True

    # Chainlit 기본 편집은 UI에서 수정 메시지 이후를 제거한다.
    # 앱 내부 prompt history도 같은 지점으로 되감아 수정본만 새 턴으로 생성한다.
    pruned_history = history[:edit_idx]
    recent_responses = _recent_responses_from_history(pruned_history)
    cl.user_session.set("conversation_history", pruned_history)
    cl.user_session.set("recent_responses", recent_responses)
    cl.user_session.set("pending_commit", None)
    if pending:
        discard_pending_commit(pending, *(_wv()[0:3]))

    if pending and pending.get("response_msg_id"):
        await _remove_message_if_present(pending.get("response_msg_id"))
    if pending and pending.get("prev_game_time"):
        await restore_game_time(pending["prev_game_time"])

    await _run_generation(
        user_input,
        pruned_history,
        recent_responses,
        step_suffix="(수정)",
        user_msg_id=msg_id,
    )
    return True


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

    # 삭제된 메시지가 pending_commit과 연관되면 pending을 취소;
    # 그렇지 않으면 history_snapshot에서도 해당 항목 제거 (reroll 시 삭제 메시지 재진입 방지)
    pending = cl.user_session.get("pending_commit")
    if pending:
        if (
            pending.get("response_msg_id") == msg_id
            or pending.get("user_msg_id") == msg_id
        ):
            cl.user_session.set("pending_commit", None)
            discard_pending_commit(pending, *(_wv()[0:3]))
        elif pending.get("history_snapshot"):
            snapshot = [h for h in pending["history_snapshot"] if h.get("msg_id") != msg_id]
            pending["history_snapshot"] = snapshot
            cl.user_session.set("pending_commit", pending)
            save_pending_commit(pending, *(_wv()[0:3]))


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
    cl.user_session.set("pending_kakao_messages", [])
    cl.user_session.set("social_media_overrides", {})
    cl.user_session.set("debug_graph_msg_id",     None)
    cl.user_session.set("traits_initialized",     False)

    if opening_scene:
        await cl.Message(content=_mark_ui_message(opening_scene, "actor"), author="세계").send()
    await inject_time_theme(hour_from_time_string(await snapshot_game_time()))
    await send_kakao_panel(
        cl.user_session.get("pc_id"),
        cl.user_session.get("npc_id"),
        current_time=await _current_game_datetime(),
        features=_social_media_features(),
    )


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """기존 채팅방을 재개할 때 세션 변수를 스레드 데이터로 복원합니다."""
    world_id, scenario_id = _resolve_thread_world(thread, WORLD_ID)
    thread_id = thread["id"]

    await _init_session_world(world_id, thread_id, scenario_id)
    try:
        await cl.data_layer.update_thread(
            thread_id=thread_id,
            metadata={"world_id": world_id, "scenario_id": scenario_id},
            tags=[f"{world_id}/{scenario_id}" if scenario_id else world_id],
        )
    except Exception:
        pass

    steps = thread.get("steps") or []
    await _remove_legacy_graph_steps(steps)
    history, recents = build_history_from_steps(
        steps,
        max_history_turns=MAX_HISTORY_TURNS,
        recent_story_turns=RECENT_STORY_TURNS,
    )
    history = [
        {**item, "content": _strip_ui_markers(item.get("content", ""))}
        for item in history
    ]
    recents = [_strip_ui_markers(item) for item in recents]

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
    cl.user_session.set("pending_kakao_messages", [])
    cl.user_session.set("social_media_overrides", {})
    cl.user_session.set("debug_graph_msg_id",     None)
    cl.user_session.set("traits_initialized",     False)

    await _ensure_session_traits_initialized()
    await inject_time_theme(hour_from_time_string(await snapshot_game_time()))
    await send_kakao_panel(
        cl.user_session.get("pc_id"),
        cl.user_session.get("npc_id"),
        current_time=await _current_game_datetime(),
        features=_social_media_features(),
    )
    await _upsert_debug_graph_best_effort(
        pc_id=cl.user_session.get("pc_id"),
        npc_id=cl.user_session.get("npc_id"),
        world_id=world_id,
    )


@cl.on_chat_end
async def on_chat_end() -> None:
    """채팅 종료 시 미확정 pending을 세션 종료 전에 강제 처리합니다."""
    pending = cl.user_session.get("pending_commit")
    if pending:
        world_id, pc_id, npc_id, _, world_config = _wv()
        try:
            await commit_pending(
                pending=pending,
                world_id=world_id,
                pc_id=pc_id,
                npc_id=npc_id,
                world_config=world_config,
                scheduler=_get_scheduler(world_id),
                show_toast=False,
            )
            cl.user_session.set("pending_commit", None)
            discard_pending_commit(pending, world_id, pc_id, npc_id)
        except Exception as exc:
            if not _is_closed_connection_error(exc):
                raise
    try:
        _close_session_driver()
    except Exception as exc:
        if not _is_closed_connection_error(exc):
            raise
        return


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

    user_input = _strip_ui_markers(message.content).strip()
    if user_input.startswith("__SOCIAL_PANEL__:"):
        await message.remove()
        await _handle_social_panel_event(user_input.removeprefix("__SOCIAL_PANEL__:"))
        return

    if user_input.startswith("__KAKAO_PANEL__:"):
        await message.remove()
        await _commit_pending_if_any_quiet(
            world_id=world_id,
            pc_id=pc_id,
            npc_id=npc_id,
            world_config=world_config,
            scheduler=_get_scheduler(world_id),
        )
        await _handle_kakao_panel_event(user_input.removeprefix("__KAKAO_PANEL__:"))
        return

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
        await _reroll_pending_response(world_id=world_id, pc_id=pc_id, npc_id=npc_id)
        return

    if await _handle_user_message_edit(message, user_input):
        return

    await _commit_pending_if_any_quiet(
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        world_config=world_config,
        scheduler=_get_scheduler(world_id),
    )
    if not await _upsert_debug_graph_best_effort(pc_id=pc_id, npc_id=npc_id, world_id=world_id):
        return
    await send_kakao_panel(
        pc_id,
        npc_id,
        current_time=await _current_game_datetime(),
        features=_social_media_features(),
    )

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
        await _ui_toast("이전 OOC 변경을 이번 턴에 반영합니다.", "info")
        route = route_user_input(user_input, message)

    # ── OOC 처리 ─────────────────────────────────────────────
    ooc_result: dict | None = None
    if is_ooc(user_input):
        ooc_changes: dict = {}
        await _ui_toast("OOC 변경 사항을 적용하는 중입니다.", "info")
        result = await parse_ooc(
            user_input,
            npc_id,
            npc_name_kor,
            pc_id=pc_id,
            world_config=world_config,
        )
        ooc_result = result
        ooc_changes = result.get("state_changes", {})
        await _ui_toast(result.get("summary") or "OOC 변경 사항을 적용했습니다.", "success")

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
            world_config=world_config,
        )
        if named_updates:
            await _ui_toast(f"보조 NPC 상태 {len(named_updates)}건을 갱신했습니다.", "success")
        await _sses_advance_slot_if_needed(world_id, ooc_changes)
        if not await _upsert_debug_graph_best_effort(pc_id=pc_id, npc_id=npc_id, world_id=world_id):
            return

        if route == TurnInputType.OOC_PATCH:
            # *마커* 를 벗겨야 actor가 '도'(also) 를 이전 context 인물과 연관짓지 않는다.
            user_input = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", r"\1", user_input, flags=re.DOTALL).strip()
            if not user_input:
                return
            route = TurnInputType.ROLEPLAY

    # ── RP 턴: 유저 메시지에 삭제 버튼 추가 + UI 마커 삽입 ─────────
    # 실제 프롬프트/히스토리는 user_input 원문을 사용하고, 화면 표시용 메시지에만 마커를 붙입니다.
    message.content = _mark_ui_message(user_input, "user")
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
    await _upsert_debug_graph_best_effort(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
