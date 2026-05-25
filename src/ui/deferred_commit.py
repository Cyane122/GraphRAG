# ================================
# src/ui/deferred_commit.py
#
# 리롤 보호를 위해 지연된 Actor 응답 확정 처리를 수행합니다.
#
# Functions
#   - commit_pending(pending: dict, world_id: str, pc_id: str, npc_id: str, world_config: dict, updating_msgs: list[str] | None = None, updated_msgs: list[str] | None = None, scheduler: object | None = None, show_toast: bool = True) -> None : pending 응답을 DB와 로그에 확정
#   - commit_pending_if_any(world_id: str, pc_id: str, npc_id: str, world_config: dict, updating_msgs: list[str], updated_msgs: list[str], scheduler: object | None = None) -> None : 세션 pending이 있으면 확정 후 제거
# ================================

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import datetime

import chainlit as cl

from src.agents.context.scene_state import update_scene_state_after_response
from src.agents.manager.effects import commit_manager_auxiliary_effects, commit_manager_core_effects
from src.core.logging.conversation_logger import append_turn
from src.simulation.state.updater import process_actor_response
from src.simulation.systems.kakao import commit_kakao_effects
from src.ui.pending_store import (
    discard_pending_commit,
    load_pending_commit,
    update_pending_status,
)
from src.ui.status import send_status_toast


async def _do_compress_narrative(recent_turns: list[dict], npc_id: str, pc_id: str) -> None:
    """커밋 확정 후 타임라인 로그를 백그라운드 압축합니다."""
    from src.simulation.systems.memory.narrative import compress_to_narrative_log
    from src.ui.time_state import snapshot_game_time
    current_time_str = await snapshot_game_time()
    current_dt = None
    if current_time_str:
        try:
            current_dt = datetime.fromisoformat(current_time_str)
        except Exception:
            pass
    await compress_to_narrative_log(recent_turns, current_dt, npc_id, pc_id)


def _commit_scene_state(pending: dict, world_id: str, pc_id: str, npc_id: str) -> None:
    """Accepted Actor response로 lightweight SceneState를 갱신합니다."""
    manager_effects = pending.get("manager_effects") or {}
    scene_state = manager_effects.get("scene_state") or {}
    update_scene_state_after_response(
        world_id=world_id,
        pc_id=pc_id,
        npc_id=npc_id,
        user_input=pending.get("user_input", ""),
        actor_response=pending.get("ai_response", ""),
        scene_types=pending.get("scene_types") or [],
        scene_chars=pending.get("scene_chars") or [],
        location=scene_state.get("location"),
    )


def _should_run_scheduler(pending: dict) -> bool:
    """Planner가 장기/사회 맥락을 선택한 턴인지 확인합니다."""
    manager_effects = pending.get("manager_effects") or {}
    context_plan = manager_effects.get("context_plan") or {}
    required_systems = set(context_plan.get("required_systems") or [])
    query_focus = set(context_plan.get("query_focus") or [])
    return bool({"goals", "social"} & required_systems or {"long_term_pressure", "nearby_activity"} & query_focus)


def _completed_stages(pending: dict) -> set[str]:
    """Return the set of durable commit stages already completed."""
    return set(pending.get("completed_stages") or [])


def _timestamp_from_pending(pending: dict) -> datetime | None:
    """Parse a pending timestamp value for log routing."""
    timestamp = pending.get("timestamp")
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            return None
    return None


def _coerce_datetime(value: object) -> datetime | None:
    """Return a datetime from an in-memory or durable pending value."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _mark_stage_done(pending: dict, world_id: str, pc_id: str, npc_id: str, stage: str) -> None:
    """Record one completed stage in session and durable storage."""
    update_pending_status(
        pending,
        world_id,
        pc_id,
        npc_id,
        status="committing",
        completed_stage=stage,
    )
    cl.user_session.set("pending_commit", pending)


async def commit_pending(
    pending: dict,
    world_id: str,
    pc_id: str,
    npc_id: str,
    world_config: dict,
    updating_msgs: list[str] | None = None,
    updated_msgs: list[str] | None = None,
    scheduler: Callable[[], Awaitable[str | None]] | None = None,
    show_toast: bool = True,
) -> None:
    """이전 턴의 Actor 응답을 DB, 로그, SceneState에 확정 반영합니다."""
    toast = None
    if show_toast and updating_msgs:
        toast = await send_status_toast(random.choice(updating_msgs))

    try:
        pending = update_pending_status(pending, world_id, pc_id, npc_id, status="committing")
        cl.user_session.set("pending_commit", pending)
        manager_effects = pending.get("manager_effects")
        completed = _completed_stages(pending)
        core_result = pending.get("core_result") or {}
        if core_result.get("current_dt"):
            core_result["current_dt"] = _coerce_datetime(core_result.get("current_dt"))

        if "manager_core" not in completed:
            try:
                core_result = await commit_manager_core_effects(
                    manager_effects,
                    pc_id=pc_id,
                    npc_id=npc_id,
                )
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "manager_core", str(exc))
                raise
            pending["core_result"] = core_result
            _mark_stage_done(pending, world_id, pc_id, npc_id, "manager_core")
            completed = _completed_stages(pending)

        if "kakao_effects" not in completed:
            try:
                await commit_kakao_effects((manager_effects or {}).get("kakao_effects") or [])
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "kakao_effects", str(exc))
                raise
            _mark_stage_done(pending, world_id, pc_id, npc_id, "kakao_effects")
            completed = _completed_stages(pending)

        if "actor_response" not in completed:
            try:
                ooc_from_pregnancy = await process_actor_response(
                    pending["ai_response"],
                    npc_id,
                    pc_id,
                    scene_types=pending.get("scene_types"),
                    scene_chars=pending.get("scene_chars", []),
                    world_config=world_config,
                    manager_effects=manager_effects,
                    history_snapshot=pending.get("history_snapshot", []),
                    recent_snapshot=pending.get("recent_snapshot", []),
                    thread_id=pending.get("thread_id"),
                    commit_id=pending.get("commit_id"),
                    user_input=pending.get("user_input", ""),
                )
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "actor_response", str(exc))
                raise
            if ooc_from_pregnancy:
                cl.user_session.set("pending_ooc", ooc_from_pregnancy)
            _mark_stage_done(pending, world_id, pc_id, npc_id, "actor_response")
            completed = _completed_stages(pending)

        if "scene_state" not in completed:
            try:
                _commit_scene_state(pending, world_id, pc_id, npc_id)
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "scene_state", str(exc))
                raise
            _mark_stage_done(pending, world_id, pc_id, npc_id, "scene_state")
            completed = _completed_stages(pending)

        if "conversation_log" not in completed:
            try:
                append_turn(
                    user_input=pending["user_input"],
                    ai_response=pending["ai_response"],
                    timestamp=_timestamp_from_pending(pending),
                )
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "conversation_log", str(exc))
                raise
            _mark_stage_done(pending, world_id, pc_id, npc_id, "conversation_log")
            completed = _completed_stages(pending)

        if "manager_auxiliary" not in completed:
            try:
                needs_result = await commit_manager_auxiliary_effects(
                    manager_effects,
                    pc_id=pc_id,
                    npc_id=npc_id,
                    current_dt=core_result.get("current_dt"),
                    scene_chars=pending.get("scene_chars", []),
                )
            except Exception as exc:
                update_pending_status(pending, world_id, pc_id, npc_id, "failed", "manager_auxiliary", str(exc))
                raise
            scene_need_hints = needs_result.get("scene_need_hints") or {}
            cl.user_session.set("scene_need_hints", scene_need_hints)
            _mark_stage_done(pending, world_id, pc_id, npc_id, "manager_auxiliary")

        if world_id == "sses" and scheduler and _should_run_scheduler(pending):
            try:
                sms = await scheduler()
            except Exception as e:
                print(f"[CommitPending] scheduler failed (ignored): {e}")
                sms = None
            if sms:
                await cl.Message(content=sms, author="사회정서지원과").send()
    finally:
        if toast:
            try:
                await toast.remove()
            except Exception:
                pass

    update_pending_status(pending, world_id, pc_id, npc_id, status="committed")
    if show_toast and updated_msgs:
        toast = await send_status_toast(random.choice(updated_msgs))
        await asyncio.sleep(1.5)
        await toast.remove()


async def commit_pending_if_any(
    world_id: str,
    pc_id: str,
    npc_id: str,
    world_config: dict,
    updating_msgs: list[str],
    updated_msgs: list[str],
    scheduler: Callable[[], Awaitable[str | None]] | None = None,
) -> None:
    """세션에 pending 응답이 있으면 확정 처리하고 pending을 제거합니다."""
    pending = cl.user_session.get("pending_commit") or load_pending_commit(world_id, pc_id, npc_id)
    if not pending:
        return
    cl.user_session.set("pending_commit", pending)
    committed = False
    try:
        await commit_pending(
            pending=pending,
            world_id=world_id,
            pc_id=pc_id,
            npc_id=npc_id,
            world_config=world_config,
            updating_msgs=updating_msgs,
            updated_msgs=updated_msgs,
            scheduler=scheduler,
            show_toast=True,
        )
        committed = True
    except Exception as e:
        print(f"[CommitPending] core commit failed; pending retained: {e}")
    finally:
        if committed:
            cl.user_session.set("pending_commit", None)
            discard_pending_commit(pending, world_id, pc_id, npc_id)
            narrative_turns: list[dict] = cl.user_session.get("narrative_turns") or []
            narrative_turns.append({"user": pending["user_input"], "actor": pending["ai_response"]})
            if len(narrative_turns) >= 10:
                asyncio.create_task(_do_compress_narrative(list(narrative_turns), npc_id, pc_id))
                narrative_turns = []
            cl.user_session.set("narrative_turns", narrative_turns)
