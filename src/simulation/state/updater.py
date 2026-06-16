# ================================
# src/simulation/state/updater.py
#
# Accepted actor responses are converted into persistent graph changes.
# This module orchestrates state, relationships, events, time plans,
# and long-running postprocessors. Primary LLM extraction lives in
# src/simulation/state/extract/primary.py.
#
# Functions
#   - process_actor_response(actor_response, npc_id, pc_id, scene_types, scene_chars, world_config, manager_effects, history_snapshot, recent_snapshot, thread_id, commit_id, user_input) -> str | None : Apply accepted actor response side effects.
#   - guard_actor_response(actor_response, npc_id, pc_id, world_config) -> dict : Validate actor response before DB writes.
#   - build_time_plan(plan, base_time) -> dict : Compute time changes without DB writes.
#   - commit_time_plan(time_plan, pc_id, npc_id, companion_ids) -> datetime : Persist a computed time plan.
#   - apply_time_updates(plan, base_time, pc_id, npc_id) -> datetime : Compute and persist time changes.
#   - delegate_complex_update(actor_response, npc_id, pc_id, initial_changes, event_only, world_config, scene_chars) -> str | None : Run complex updates for event-only paths.
#   - _await_relationship_task(relationship_task) -> None : Best-effort await for the parallel secondary-relationship task.
#   - _should_run_auxiliary_character_updates_with_log(actor_response, participant_ids, context_plan, world_config, scene_chars) -> bool : Gate auxiliary extractors and print skip context.
#   - _write_updater_diff_snapshot(plan, state_candidates, rel_candidate, event_candidate) -> None : LLM 출력과 diff 결과를 logs/updater_diff.json에 저장.
#   - _select_event_owner_id(npc_id, pc_id, participant_ids) -> str | None : Choose the relationship anchor for Event updates.
#   - _apply_event_action(plan, event_allowed, active_event, actor_response, event_owner_id, pc_id, participant_ids, state_candidates, rel_candidate, guard, feasibility_audit, commit_id) -> dict | None : Apply Event action from an updater plan.
# ================================
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from src.core.database import (
    get_dynamic_state_field_types,
    update_dynamic_state,
    update_relationship_affinity,
)
from src.config import TURN_EXTRACTOR_MODE
from src.simulation.systems.world_dynamics.organic import process_ejaculation
from src.simulation.state.apply.audit import (
    _audit_event_candidate,
    _audit_relationship_delta,
    _audit_state_updates,
    _audit_time_location_schedule,
    _needs_classification,
    _safe_int,
    _sanitize_stress_level,
    _write_state_audit_snapshot,
    guard_actor_response,
)
from src.simulation.state.extract.creator_slots import apply_creator_slot_updates
from src.simulation.state.extract.dynamic_information import (
    apply_multi_character_dynamic_information_updates,
)
from src.simulation.state.apply.events import (
    _append_to_event,
    _apply_relationship_status,
    _close_event,
    _create_event,
    _fetch_active_event,
    _get_current_iso_time,
    _update_acceptance_scores,
    delegate_complex_update,
    update_relationship_narrative,
)
from src.simulation.state.extract.multi_character import apply_multi_character_state_updates
from src.simulation.state.apply.relationships import apply_scene_relationship_updates
from src.simulation.state.apply.time_plan import (
    apply_time_updates,
    build_time_plan,
    commit_time_plan,
)
from src.simulation.state.extract.turn_extractor import (
    facts_to_primary_plan,
    load_or_extract_turn_facts,
    write_extractor_shadow_diff,
)
from src.simulation.state.apply.update_policy import (
    has_event_signal,
    should_run_auxiliary_character_updates,
    should_run_life_depth_system,
    should_run_secondary_relationship_updates,
)
from src.simulation.state.extract.primary import _run_primary_update


# Primary and auxiliary LLM workers for accepted response updates.


async def _run_auxiliary_character_updates(
    actor_response: str,
    pc_id: str,
    scene_types: list[str] | None,
    participant_ids: list[str],
    world_config: dict | None = None,
) -> None:
    """Run auxiliary multi-character extractors without blocking main state writes."""
    results = await asyncio.gather(
        apply_multi_character_state_updates(
            actor_response,
            pc_id,
            participant_ids=participant_ids,
            world_config=world_config,
        ),
        apply_multi_character_dynamic_information_updates(
            actor_response,
            pc_id,
            scene_types=scene_types,
            participant_ids=participant_ids,
        ),
        apply_creator_slot_updates(
            actor_response,
            participant_ids=participant_ids,
            world_config=world_config,
        ),
        return_exceptions=True,
    )
    labels = ("multi-character state", "dynamic information", "creator slots")
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            print(f"[StateUpdater] auxiliary {label} update failed (ignored): {result}")


async def _await_relationship_task(relationship_task) -> None:
    """병렬 실행된 2차 관계 업데이트 태스크를 best-effort로 await한다(예외는 무시·로깅).

    순차 호출 시절의 try/except 동작을 보존한다: apply_scene_relationship_updates의
    DB 조회/쓰기가 실패해도 턴 처리를 중단시키지 않는다.
    """
    if relationship_task is None:
        return
    try:
        await relationship_task
    except Exception as e:
        print(f"[RelationshipUpdater] secondary update failed (ignored): {e}")


def _should_run_auxiliary_character_updates_with_log(
    actor_response: str,
    participant_ids: list[str],
    context_plan: dict | None,
    world_config: dict | None,
    scene_chars: list[str] | None,
) -> bool:
    """Gate auxiliary state extractors and print skip context for debugging."""
    should_run = should_run_auxiliary_character_updates(
        actor_response,
        participant_ids,
        context_plan,
        world_config,
    )
    if should_run or not actor_response.strip():
        return should_run
    required_systems = (context_plan or {}).get("required_systems") or []
    importance = (context_plan or {}).get("importance")
    print(
        "[MultiStateUpdater] skipped by policy: "
        f"participants={participant_ids}, scene_chars={scene_chars or []}, "
        f"required_systems={required_systems}, importance={importance}"
    )
    return False




def _render_recent_event_context(
    history_snapshot: list[dict] | None = None,
    recent_snapshot: list[str] | None = None,
) -> str:
    """Render recent accepted thread context for Event creation without adding world-specific rules."""
    lines: list[str] = []
    for item in (history_snapshot or [])[-8:]:
        role = item.get("role") or "unknown"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[-800:]}")
    if not lines:
        for idx, content in enumerate((recent_snapshot or [])[-4:], start=1):
            text = str(content or "").strip()
            if text:
                lines.append(f"recent_{idx}: {text[-800:]}")
    return "\n\n".join(lines)


_LOGS_DIR = Path("logs")


def _write_updater_diff_snapshot(
    plan: dict,
    state_candidates: list[dict],
    rel_candidate: dict | None,
    event_candidate: dict | None,
) -> None:
    """LLM 원본 출력과 diff 결과를 logs/updater_diff.json에 저장한다 (매 턴 덮어쓰기)."""
    try:
        _LOGS_DIR.mkdir(exist_ok=True)
        payload = {
            "raw_plan": plan,
            "state_candidates": state_candidates,
            "relationship_candidate": rel_candidate,
            "event_candidate": event_candidate,
        }
        (_LOGS_DIR / "updater_diff.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[StateAudit] updater_diff 저장 실패: {e}")


def _fmt_state_diff(state_candidates: list[dict]) -> str:
    """state_candidates를 commit/hold 구분한 한 줄 요약으로 변환한다."""
    committed = [c for c in state_candidates if c.get("commit_policy") == "commit"]
    held = [c for c in state_candidates if c.get("commit_policy") == "hold"]
    parts = " | ".join(f"{c['field']}={c['new_value']}" for c in committed)
    if held:
        held_fields = ",".join(c["field"] for c in held)
        parts = f"{parts}  (held: {held_fields})" if parts else f"(held only: {held_fields})"
    return parts


# State update main path


def _select_event_owner_id(
    npc_id: str,
    pc_id: str,
    participant_ids: list[str],
) -> str | None:
    """Return the character id whose relationship should anchor Event updates."""
    if npc_id != pc_id:
        return npc_id
    for char_id in participant_ids:
        if char_id and char_id != pc_id:
            return char_id
    return None


async def _apply_event_action(
    plan: dict,
    event_allowed: bool,
    active_event: dict | None,
    actor_response: str,
    event_owner_id: str,
    pc_id: str,
    participant_ids: list[str],
    state_candidates: list[dict],
    rel_candidate: dict | None,
    guard: dict,
    feasibility_audit: dict,
    commit_id: str = "",
) -> dict | None:
    """Create, continue, or close an Event from a primary updater plan."""
    new_event: dict | None = None
    event_candidate: dict | None = None

    action = plan.get("action", "none") if event_allowed else "none"

    if action == "continue" and active_event:
        await _append_to_event(active_event["id"], actor_response)

    elif action == "close" and active_event:
        await _close_event(active_event["id"], event_owner_id, pc_id, actor_response)

    elif action in ("close_create", "create"):
        if active_event and action == "close_create":
            await _close_event(active_event["id"], event_owner_id, pc_id, actor_response)
        new_event, event_candidate = _audit_event_candidate(plan.get("new_event"), actor_response)

    else:
        if event_allowed and plan.get("new_event"):
            new_event, event_candidate = _audit_event_candidate(plan.get("new_event"), actor_response)

    if event_candidate:
        ev_summary = str(event_candidate.get("evidence") or event_candidate.get("new_value") or "")[:60]
        print(f"[EventDiff] {ev_summary} [{event_candidate['commit_policy']}]")

    _write_updater_diff_snapshot(plan, state_candidates, rel_candidate, event_candidate)
    _write_state_audit_snapshot(
        actor_response          = actor_response,
        npc_id                  = event_owner_id,
        pc_id                   = pc_id,
        guard                   = guard,
        state_candidates        = state_candidates,
        relationship_candidate  = rel_candidate,
        event_candidate         = event_candidate,
        feasibility_audit       = feasibility_audit,
    )

    if not new_event:
        return None

    await _create_event(
        new_event,
        event_owner_id,
        pc_id,
        actor_response,
        participant_ids=participant_ids,
        commit_id=commit_id,
    )
    new_status = new_event.get("new_relationship_status")
    event_importance = _safe_int(new_event.get("importance"), 0)
    if new_status and event_importance >= 7:
        await _apply_relationship_status(event_owner_id, pc_id, new_status)
    if event_importance >= 6:
        asyncio.create_task(update_relationship_narrative(
            event_owner_id, pc_id, new_event.get("summary", ""), event_importance,
        ))
    return new_event


_ANALYZE_BLOCK_RE = re.compile(r"<analyze>[\s\S]*?</analyze>", re.IGNORECASE)


def _strip_analyze_block(text: str) -> str:
    """Actor 응답에서 <analyze>…</analyze> CoT 블록을 제거하고 가시 산문만 남긴다."""
    return _ANALYZE_BLOCK_RE.sub("", text or "").strip()


async def process_actor_response(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    scene_types:    list[str] | None = None,
    scene_chars:    list[str] | None = None,
    world_config:   dict | None = None,
    manager_effects: dict | None = None,
    history_snapshot: list[dict] | None = None,
    recent_snapshot: list[str] | None = None,
    thread_id: str | None = None,
    commit_id: str | None = None,
    user_input: str = "",
) -> str | None:
    """
    Analyze an accepted Actor response and apply persistent state side effects.
    Returns an OOC pregnancy/organic system message if one is generated, otherwise None.
    """
    from src.simulation.systems.social import ensure_scene_relationships, resolve_and_update as wb_resolve

    # Actor 응답은 <analyze> CoT 블록 + 가시 산문으로 구성된다. 상태 추출과 가드는 모델의
    # 사적 사고가 아니라 실제 산문을 대상으로 해야 하므로 분석 블록을 먼저 제거한다.
    # (제거 전에는 [:N] 절단이 긴 CoT에 잠식되어 후반 산문의 상태 변화를 놓칠 수 있었다.)
    actor_response = _strip_analyze_block(actor_response)

    guard = guard_actor_response(actor_response, npc_id, pc_id, world_config)
    feasibility_audit = _audit_time_location_schedule(manager_effects)
    if not guard["passed"]:
        codes = ", ".join(i["code"] for i in guard.get("issues", []))
        print(f"[StateGuard] rejected: {codes}")
        _write_state_audit_snapshot(
            actor_response,
            npc_id,
            pc_id,
            guard,
            feasibility_audit=feasibility_audit,
        )
        return None
    if guard["issues"]:
        codes = ", ".join(f"{i['code']}({i['severity']})" for i in guard.get("issues", []))
        print(f"[StateGuard] warning: {codes}")
    recent_event_context = _render_recent_event_context(history_snapshot, recent_snapshot)
    context_plan = (manager_effects or {}).get("context_plan") or {}

    participant_ids = [pc_id, npc_id]
    if world_config and scene_chars:
        try:
            manager_scene_ids = (manager_effects or {}).get("scene_npc_ids") or []
            allowed_existing_ids = [
                pc_id,
                npc_id,
                *manager_scene_ids,
            ]
            resolved_ids = await wb_resolve(
                scene_chars,
                npc_id,
                pc_id,
                world_config,
                allowed_existing_ids=allowed_existing_ids,
                source_text=actor_response,
            )
            participant_ids = list(dict.fromkeys([pc_id, npc_id, *resolved_ids]))
        except Exception as e:
            print(f"[WorldBuilder] early resolve failed (ignored): {e}")

    if npc_id != pc_id:
        try:
            await ensure_scene_relationships(participant_ids)
        except Exception as e:
            print(f"[RelationshipUpdater] primary relationship ensure failed (ignored): {e}")

    event_owner_id = _select_event_owner_id(npc_id, pc_id, participant_ids)

    # 현재 열린 이벤트 조회 (event owner / pc 쌍 기준)
    active_event: dict | None = None
    if event_owner_id:
        try:
            active_event = await _fetch_active_event(event_owner_id, pc_id)
        except Exception as _ae_err:
            print(f"[EventAccum] active_event fetch failed (ignored): {_ae_err}")

    event_allowed = bool(event_owner_id) and has_event_signal(
        actor_response,
        participant_ids,
        manager_effects,
        active_event,
    )
    extractor_mode = "legacy" if (manager_effects or {}).get("ooc_patch_result") else TURN_EXTRACTOR_MODE
    turn_facts, extractor_metrics = await load_or_extract_turn_facts(
        actor_response=actor_response,
        user_input=user_input,
        thread_id=thread_id,
        commit_id=commit_id,
        npc_id=npc_id,
        pc_id=pc_id,
        scene_types=scene_types,
        scene_chars=scene_chars,
        mode=extractor_mode,
    )

    if scene_types and not _needs_classification(actor_response, scene_types) and not event_allowed:
        print("[StateUpdater] skipped (no state-relevant change)")
        if _should_run_auxiliary_character_updates_with_log(
            actor_response,
            participant_ids,
            context_plan,
            world_config,
            scene_chars,
        ):
            await _run_auxiliary_character_updates(
                actor_response,
                pc_id,
                scene_types,
                participant_ids,
                world_config=world_config,
            )
        if should_run_secondary_relationship_updates(participant_ids):
            await apply_scene_relationship_updates(
                actor_response,
                participant_ids,
                primary_pair=(npc_id, pc_id),
            )
        write_extractor_shadow_diff(thread_id, commit_id, turn_facts, None, extractor_metrics)
        return None

    if npc_id == pc_id:
        # NPC=PC self-state path keeps PC state local; Event extraction uses a scene partner when available.
        # multi_character 와 dynamic_info 는 독립적이므로 병렬 실행
        print("[StateUpdater] NPC=PC: DynamicState update only")
        if _should_run_auxiliary_character_updates_with_log(
            actor_response,
            participant_ids,
            context_plan,
            world_config,
            scene_chars,
        ):
            await _run_auxiliary_character_updates(
                actor_response,
                pc_id,
                scene_types,
                participant_ids,
                world_config=world_config,
            )
        state_plan: dict | None = None
        try:
            if extractor_mode == "unified" and turn_facts:
                state_plan = facts_to_primary_plan(turn_facts, allow_event=False, npc_id=npc_id, pc_id=pc_id)
                if not state_plan.get("dynamic_state"):
                    extractor_metrics["global_fallback_count"] = extractor_metrics.get("global_fallback_count", 0) + 1
                    state_plan = await _run_primary_update(
                        actor_response,
                        npc_id,
                        pc_id,
                        world_config,
                        allow_event=False,
                    )
            else:
                state_plan = await _run_primary_update(
                    actor_response,
                    npc_id,
                    pc_id,
                    world_config,
                    allow_event=False,
                )
            state = dict(state_plan.get("dynamic_state") or {})
            for field in ("stress_level", "workplace_stress_level"):
                if field in state:
                    sanitized = _sanitize_stress_level(state[field])
                    if sanitized is None:
                        del state[field]
                    else:
                        state[field] = sanitized
            state, _ = _audit_state_updates(state, actor_response, npc_id)
            if state:
                await update_dynamic_state(npc_id, state)
        except Exception as e:
            print(f"[StateUpdater] NPC=PC DynamicState update failed (continuing): {e}")
        if event_allowed and event_owner_id:
            try:
                event_plan = await _run_primary_update(
                    actor_response,
                    event_owner_id,
                    pc_id,
                    world_config,
                    recent_event_context,
                    active_event,
                    allow_event=True,
                )
                if event_plan:
                    await _apply_event_action(
                        event_plan,
                        event_allowed,
                        active_event,
                        actor_response,
                        event_owner_id,
                        pc_id,
                        participant_ids,
                        [],
                        None,
                        guard,
                        feasibility_audit,
                        commit_id=commit_id or "",
                    )
            except Exception as e:
                print(f"[StateUpdater] NPC=PC event update failed (continuing): {e}")
        if should_run_secondary_relationship_updates(participant_ids):
            try:
                await apply_scene_relationship_updates(
                    actor_response,
                    participant_ids,
                    primary_pair=None,
                )
            except Exception as e:
                print(f"[WorldBuilder] resolve failed (continuing): {e}")
        if should_run_life_depth_system(
            "organic", actor_response, context_plan, 0, 0, scene_types
        ):
            try:
                # NPC==PC path can still affect a scene partner; organic.py narrows
                # scene_chars to explicitly mentioned or single-partner candidates.
                organic_message = await process_ejaculation(
                    npc_id, actor_response,
                    scene_char_ids=scene_chars,
                    father_id=pc_id,
                )
                write_extractor_shadow_diff(thread_id, commit_id, turn_facts, state_plan, extractor_metrics)
                return organic_message
            except Exception as e:
                print(f"[PregnancyMgr] processing failed (continuing): {e}")
        write_extractor_shadow_diff(thread_id, commit_id, turn_facts, state_plan, extractor_metrics)
        return None

    # Auxiliary character extraction is independent, but only useful on multi-character or high-signal turns.
    auxiliary_task = None
    if _should_run_auxiliary_character_updates_with_log(
        actor_response,
        participant_ids,
        context_plan,
        world_config,
        scene_chars,
    ):
        auxiliary_task = asyncio.create_task(_run_auxiliary_character_updates(
            actor_response,
            pc_id,
            scene_types,
            participant_ids,
            world_config=world_config,
        ))
    # 2차 관계 업데이트(Pro)는 주 쌍(npc,pc)을 제외한 별개 RELATIONSHIP 엣지만 건드리므로
    # primary/auxiliary 업데이트와 병렬로 실행해 순차 지연(~7s)을 제거한다.
    relationship_task = None
    if should_run_secondary_relationship_updates(participant_ids):
        relationship_task = asyncio.create_task(apply_scene_relationship_updates(
            actor_response,
            participant_ids,
            primary_pair=(npc_id, pc_id),
        ))
    try:
        if extractor_mode == "unified" and turn_facts:
            plan = facts_to_primary_plan(turn_facts, allow_event=event_allowed, npc_id=npc_id, pc_id=pc_id)
            if not any((plan.get("dynamic_state"), plan.get("relationship_delta"), plan.get("new_event"))):
                extractor_metrics["global_fallback_count"] = extractor_metrics.get("global_fallback_count", 0) + 1
                plan = await _run_primary_update(
                    actor_response,
                    npc_id,
                    pc_id,
                    world_config,
                    recent_event_context,
                    active_event,
                    allow_event=event_allowed,
                )
        else:
            plan = await _run_primary_update(
                actor_response,
                npc_id,
                pc_id,
                world_config,
                recent_event_context,
                active_event,
                allow_event=event_allowed,
            )
    except Exception as e:
        print(f"[StateUpdater] primary update failed (ignored): {e}")
        try:
            if auxiliary_task:
                await auxiliary_task
        finally:
            await _await_relationship_task(relationship_task)
        write_extractor_shadow_diff(thread_id, commit_id, turn_facts, None, extractor_metrics)
        return None
    try:
        if auxiliary_task:
            await auxiliary_task
    finally:
        await _await_relationship_task(relationship_task)
    if not plan:
        write_extractor_shadow_diff(thread_id, commit_id, turn_facts, plan, extractor_metrics)
        return None
    write_extractor_shadow_diff(thread_id, commit_id, turn_facts, plan, extractor_metrics)

    state_candidates: list = []
    rel_candidate: dict | None = None
    event_candidate: dict | None = None
    delta = None
    new_event = None

    # Apply DynamicState updates.
    try:
        state = dict(plan.get("dynamic_state") or {})
        for field in ("stress_level", "workplace_stress_level"):
            if field in state:
                sanitized = _sanitize_stress_level(state[field])
                if sanitized is None:
                    del state[field]
                else:
                    state[field] = sanitized
        state, state_candidates = _audit_state_updates(state, actor_response, npc_id)
        if state_candidates:
            print(f"[StateDiff] {_fmt_state_diff(state_candidates)}")
        if state:
            await update_dynamic_state(npc_id, state)
    except Exception as e:
        print(f"[StateUpdater] DynamicState update failed (ignored): {e}")

    # Apply relationship affinity delta.
    try:
        delta, rel_candidate = _audit_relationship_delta(
            plan.get("relationship_delta"), actor_response, npc_id, pc_id
        )
        if rel_candidate:
            v = rel_candidate["new_value"]
            sign = "+" if v > 0 else ""
            print(f"[RelationshipDiff] affinity {sign}{v} [{rel_candidate['commit_policy']}]")
        if delta:
            d = int(delta)
            await update_relationship_affinity(npc_id, pc_id, d)
            await update_relationship_affinity(pc_id, npc_id, d)
    except Exception as e:
        print(f"[StateUpdater] relationship update failed (ignored): {e}")

    # Apply optional TS/North acceptance scores.
    if world_config and world_config.get("ts_scoring_enabled"):
        try:
            await _update_acceptance_scores(
                npc_id,
                _safe_int(plan.get("ts_acceptance_delta"), 0),
                _safe_int(plan.get("northern_attachment_delta"), 0),
            )
        except Exception as e:
            print(f"[StateUpdater] TS scoring update failed (ignored): {e}")

    # Create/continue/close Event based on action.
    new_event: dict | None = None
    try:
        if event_owner_id:
            new_event = await _apply_event_action(
                plan,
                event_allowed,
                active_event,
                actor_response,
                event_owner_id,
                pc_id,
                participant_ids,
                state_candidates,
                rel_candidate,
                guard,
                feasibility_audit,
                commit_id=commit_id or "",
            )
    except Exception as e:
        print(f"[StateUpdater] event creation failed (ignored): {e}")

    # 2차 관계 업데이트는 위에서 relationship_task로 병렬 실행·await 완료됨(순차 호출 제거).

    # Life-depth side effects use event importance and relationship delta.
    # Gossip, memory distortion, and personality drift are intentionally best-effort.
    _d        = int(delta or 0)
    _imp      = _safe_int(new_event.get("importance"), 0) if new_event else 0
    _depth_ts = await _get_current_iso_time()
    _depth_dt = datetime.fromisoformat(_depth_ts)

    # Gossip propagation: important event plus meaningful affinity movement.
    if new_event and _imp >= 5 and abs(_d) >= 3:
        try:
            from src.simulation.systems.world_dynamics.reputation import propagate_gossip
            await propagate_gossip(
                event_summary      = new_event.get("summary", ""),
                event_importance   = _imp,
                relationship_delta = _d,
                source_npc_id      = npc_id,
                pc_id              = pc_id,
                timestamp_iso      = _depth_ts,
                source_event_id    = new_event.get("id"),
            )
        except Exception as e:
            print(f"[Updater] gossip propagation failed (ignored): {e}")

    # Large relationship swings immediately distort shared memories.
    if abs(_d) >= 10:
        try:
            from src.simulation.systems.memory import distort_on_affinity_change
            await distort_on_affinity_change(npc_id, pc_id, _d, _depth_dt)
        except Exception as e:
            print(f"[Updater] memory distortion failed (ignored): {e}")

    # Personality drift check (micro / macro).
    if should_run_life_depth_system("personality", actor_response, context_plan, _imp, _d, scene_types):
        try:
            from src.simulation.systems.world_dynamics.personality import check_personality_drift
            await check_personality_drift(
                npc_id             = npc_id,
                pc_id              = pc_id,
                relationship_delta = _d,
                event_importance   = _imp,
                current_game_time  = _depth_dt,
            )
        except Exception as e:
            print(f"[Updater] personality drift failed (ignored): {e}")

    # Life-depth postprocessors keep long-running goals, meaningful objects,
    # and conditional secrets in step with the accepted actor response.
    _event_id = new_event.get("id") if new_event else None
    if should_run_life_depth_system("goals", actor_response, context_plan, _imp, _d, scene_types):
        try:
            from src.simulation.systems.goals import apply_goal_updates

            await apply_goal_updates(
                actor_response = actor_response,
                owner_id       = npc_id,
                pc_id          = pc_id,
                current_time   = _depth_dt,
                event_id       = _event_id,
            )
        except Exception as e:
            print(f"[LifeDepth] goal update failed (ignored): {e}")

    if should_run_life_depth_system("items", actor_response, context_plan, _imp, _d, scene_types):
        try:
            from src.simulation.systems.items import apply_item_updates

            await apply_item_updates(
                actor_response = actor_response,
                owner_id       = npc_id,
                pc_id          = pc_id,
                current_time   = _depth_dt,
                event_id       = _event_id,
            )
        except Exception as e:
            print(f"[LifeDepth] item update failed (ignored): {e}")

    if should_run_life_depth_system("secrets", actor_response, context_plan, _imp, _d, scene_types):
        try:
            from src.simulation.systems.secrets import apply_secret_updates

            await apply_secret_updates(
                actor_response = actor_response,
                owner_id       = npc_id,
                pc_id          = pc_id,
                current_time   = _depth_dt,
                event_id       = _event_id,
            )
        except Exception as e:
            print(f"[LifeDepth] secret update failed (ignored): {e}")

    if should_run_life_depth_system("organic", actor_response, context_plan, _imp, _d, scene_types):
        try:
            return await process_ejaculation(npc_id, actor_response, scene_char_ids=scene_chars, father_id=pc_id)
        except Exception as e:
            print(f"[PregnancyMgr] processing failed (ignored): {e}")
    return None
