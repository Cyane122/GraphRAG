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

import chainlit as cl

from src.agents.context.scene_state import update_scene_state_after_response
from src.agents.manager.effects import commit_manager_auxiliary_effects, commit_manager_core_effects
from src.core.logging.conversation_logger import append_turn
from src.simulation.state.updater import process_actor_response
from src.ui.status import send_status_toast


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
        manager_effects = pending.get("manager_effects")
        core_result = await commit_manager_core_effects(
            manager_effects,
            pc_id=pc_id,
            npc_id=npc_id,
        )

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
        )
        if ooc_from_pregnancy:
            cl.user_session.set("pending_ooc", ooc_from_pregnancy)

        _commit_scene_state(pending, world_id, pc_id, npc_id)

        append_turn(
            user_input=pending["user_input"],
            ai_response=pending["ai_response"],
            timestamp=pending.get("timestamp"),
        )

        needs_result = await commit_manager_auxiliary_effects(
            manager_effects,
            pc_id=pc_id,
            npc_id=npc_id,
            current_dt=core_result.get("current_dt"),
            scene_chars=pending.get("scene_chars", []),
        )
        scene_need_hints = needs_result.get("scene_need_hints") or {}
        cl.user_session.set("scene_need_hints", scene_need_hints)

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
    pending = cl.user_session.get("pending_commit")
    if not pending:
        return
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
