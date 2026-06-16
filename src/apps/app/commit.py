# ================================
# src/apps/app/commit.py
#
# Chainlit-free deferred commit handling for standalone web UI conversations.
#
# Functions
#   - commit_pending_web(pending: dict, state: ConversationState, scheduler: object | None = None) -> dict[str, str] : Commit one pending response.
# ================================

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.agents.context.scene_state import update_scene_state_after_response
from src.agents.manager.effects import commit_manager_auxiliary_effects, commit_manager_core_effects
from src.core.logging.conversation_logger import append_turn
from src.simulation.state.apply.time_plan import reconcile_location_with_prose
from src.simulation.state.updater import process_actor_response
from src.simulation.systems.kakao import commit_kakao_effects
from src.apps.app.pending_store import discard_pending_commit, update_pending_status
from src.apps.app.models import ConversationState

_NARRATIVE_COMPRESS_THRESHOLD = 10


def _completed_stages(pending: dict) -> set[str]:
    """Return completed commit stages from a pending payload."""
    return set(pending.get("completed_stages") or [])


def _timestamp_from_pending(pending: dict) -> datetime | None:
    """Parse pending timestamp for conversation logging."""
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
    """Coerce a pending datetime field into datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _mark_stage_done(pending: dict, state: ConversationState, stage: str) -> None:
    """Persist one completed commit stage."""
    update_pending_status(
        pending,
        state.world_id,
        state.pc_id,
        state.npc_id,
        status="committing",
        completed_stage=stage,
    )


def _commit_scene_state(pending: dict, state: ConversationState) -> None:
    """Update lightweight SceneState after accepting an Actor response."""
    manager_effects = pending.get("manager_effects") or {}
    scene_state = manager_effects.get("scene_state") or {}
    update_scene_state_after_response(
        world_id=state.world_id,
        pc_id=state.pc_id,
        npc_id=state.npc_id,
        user_input=pending.get("user_input", ""),
        actor_response=pending.get("ai_response", ""),
        scene_types=pending.get("scene_types") or [],
        scene_chars=pending.get("scene_chars") or [],
        location=scene_state.get("location"),
    )


async def _maybe_compress_narrative(state: ConversationState) -> None:
    """누적 대화가 임계치에 도달하면 타임라인 narrative 로그로 압축한다 (Chainlit 패리티)."""
    if len(state.narrative_turns) < _NARRATIVE_COMPRESS_THRESHOLD:
        return
    from src.simulation.systems.memory.narrative import compress_to_narrative_log
    from src.apps.app.runtime import current_game_datetime

    turns = list(state.narrative_turns)
    try:
        current_dt = await current_game_datetime()
        await compress_to_narrative_log(turns, current_dt, state.npc_id, state.pc_id)
    except Exception as exc:
        # 압축 실패 시 버퍼를 비우지 않는다 → 다음 턴 narrative 단계에서 재시도된다.
        print(f"[CommitPendingWeb] narrative compression skipped (retry next turn): {exc}")
        return
    state.narrative_turns = []


async def commit_pending_web(
    pending: dict,
    state: ConversationState,
    scheduler: Any | None = None,
) -> dict[str, str]:
    """Commit a pending Actor response without Chainlit session or UI calls.

    크래시 복구 모델: 각 단계는 완료 후 `completed_stages`에 기록되고, 재진입 시 이미 완료된
    단계는 건너뛴다. 따라서 각 단계는 **재실행 안전(idempotent)** 해야 한다 — 단계가 부분
    적용된 채 죽으면(완료 기록 전) 다음 호출에서 통째로 다시 실행되기 때문이다. 새 단계를
    추가할 때 이 계약을 지킬 것(예: 증분 delta가 아니라 절대값 set, 또는 중복 가드 사용).
    `actor_response` 단계의 이벤트 생성은 source_commit_id로 dedup되지만, 상태/관계 delta는
    아직 완전 idempotent하지 않다(후속 슬라이스 대상).
    """
    del scheduler
    pending = update_pending_status(pending, state.world_id, state.pc_id, state.npc_id, status="committing")
    manager_effects = pending.get("manager_effects") or {}
    completed = _completed_stages(pending)
    core_result = pending.get("core_result") or {}
    commit_id = str(pending.get("commit_id") or "")
    print(
        "[CommitPendingWeb] start "
        f"commit_id={commit_id} thread_id={pending.get('thread_id')} "
        f"world={state.world_id} pc={state.pc_id} npc={state.npc_id} "
        f"completed={sorted(completed)}"
    )
    if core_result.get("current_dt"):
        core_result["current_dt"] = _coerce_datetime(core_result.get("current_dt"))

    if "manager_core" not in completed:
        core_result = await commit_manager_core_effects(
            manager_effects,
            pc_id=state.pc_id,
            npc_id=state.npc_id,
        )
        pending["core_result"] = core_result
        _mark_stage_done(pending, state, "manager_core")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: manager_core commit_id={commit_id}")

    if "location_reconcile" not in completed:
        # Actor 산문 헤더가 Manager 사전판정과 다른 장소를 가리키면 산문 우선으로 위치를 보정한다.
        await reconcile_location_with_prose(
            pending.get("ai_response", ""),
            state.pc_id,
            state.npc_id,
            companion_ids=manager_effects.get("scene_npc_ids") or [],
        )
        _mark_stage_done(pending, state, "location_reconcile")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: location_reconcile commit_id={commit_id}")

    if "kakao_effects" not in completed:
        await commit_kakao_effects(manager_effects.get("kakao_effects") or [])
        _mark_stage_done(pending, state, "kakao_effects")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: kakao_effects commit_id={commit_id}")

    if "actor_response" not in completed:
        ooc_from_pregnancy = await process_actor_response(
            pending["ai_response"],
            state.npc_id,
            state.pc_id,
            scene_types=pending.get("scene_types"),
            scene_chars=pending.get("scene_chars", []),
            world_config=state.world_config,
            manager_effects=manager_effects,
            history_snapshot=pending.get("history_snapshot", []),
            recent_snapshot=pending.get("recent_snapshot", []),
            thread_id=pending.get("thread_id"),
            commit_id=pending.get("commit_id"),
            user_input=pending.get("user_input", ""),
        )
        # 임신/유기 시스템이 만든 OOC는 다음 턴 입력 앞에 주입한다(service.append_user_and_stream).
        if ooc_from_pregnancy:
            state.pending_ooc = ooc_from_pregnancy
        _mark_stage_done(pending, state, "actor_response")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: actor_response commit_id={commit_id}")

    if "scene_state" not in completed:
        _commit_scene_state(pending, state)
        _mark_stage_done(pending, state, "scene_state")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: scene_state commit_id={commit_id}")

    if "conversation_log" not in completed:
        append_turn(
            user_input=pending["user_input"],
            ai_response=pending["ai_response"],
            timestamp=_timestamp_from_pending(pending),
        )
        _mark_stage_done(pending, state, "conversation_log")
        completed = _completed_stages(pending)
        print(f"[CommitPendingWeb] stage done: conversation_log commit_id={commit_id}")

    if "manager_auxiliary" not in completed:
        needs_result = await commit_manager_auxiliary_effects(
            manager_effects,
            pc_id=state.pc_id,
            npc_id=state.npc_id,
            current_dt=core_result.get("current_dt"),
            scene_chars=pending.get("scene_chars", []),
        )
        state.scene_need_hints = needs_result.get("scene_need_hints") or {}
        _mark_stage_done(pending, state, "manager_auxiliary")
        print(f"[CommitPendingWeb] stage done: manager_auxiliary commit_id={commit_id}")

    if "narrative" not in completed:
        # 커밋 확정된 턴을 narrative 버퍼에 누적하고, 임계치에서 타임라인으로 압축한다.
        state.narrative_turns.append(
            {"user": pending.get("user_input", ""), "actor": pending.get("ai_response", "")}
        )
        _mark_stage_done(pending, state, "narrative")
        completed = _completed_stages(pending)
        await _maybe_compress_narrative(state)

    update_pending_status(pending, state.world_id, state.pc_id, state.npc_id, status="committed")
    discard_pending_commit(pending, state.world_id, state.pc_id, state.npc_id)
    state.pending_commit = None
    print(f"[CommitPendingWeb] committed commit_id={commit_id}")
    return {"status": "committed"}
