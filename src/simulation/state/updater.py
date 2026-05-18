# ================================
# src/simulation/state/updater.py
#
# Accepted actor responses are converted into persistent graph changes.
# This module handles the main NPC state, relationships, events, time plans,
# and long-running postprocessors. Multi-character NPC state extraction lives
# in src/simulation/state/multi_character.py.
#
# Functions
#   - process_actor_response(actor_response: str, npc_id: str, pc_id: str, scene_types: list[str] | None, scene_chars: list[str] | None, world_config: dict | None, manager_effects: dict | None) -> str | None : Apply accepted actor response side effects.
#   - guard_actor_response(actor_response: str, npc_id: str, pc_id: str, world_config: dict | None) -> dict : Validate actor response before DB writes.
#   - build_time_plan(plan: dict, base_time: datetime) -> dict : Compute time changes without DB writes.
#   - commit_time_plan(time_plan: dict, pc_id: str, npc_id: str) -> datetime : Persist a computed time plan.
#   - apply_time_updates(plan: dict, base_time: datetime, pc_id: str, npc_id: str) -> datetime : Compute and persist time changes.
#   - delegate_complex_update(actor_response: str, npc_id: str, pc_id: str, initial_changes: dict | None, event_only: bool, world_config: dict | None, scene_chars: list[str] | None) -> str | None : Run complex updates for event-only paths.
# ================================
import asyncio
import json
import re
from datetime import datetime, timedelta

from src.core.database import (
    async_driver,
    update_dynamic_state,
    update_relationship_affinity,
    move_location,
    get_in_universe_time,
)
from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL, MODEL_EVENT_CREATOR as EVENT_MODEL
from src.core.embedding.encoder import embed_async
from src.simulation.systems.memory import ensure_memories_for_event
from src.simulation.systems.organic import process_ejaculation
from src.simulation.state.audit import (
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
from src.simulation.state.dynamic_information import (
    apply_multi_character_dynamic_information_updates,
)
from src.simulation.state.events import (
    _apply_relationship_status,
    _create_event,
    _get_current_iso_time,
    _update_acceptance_scores,
    delegate_complex_update,
    update_relationship_narrative,
)
from src.simulation.state.multi_character import apply_multi_character_state_updates
from src.simulation.state.relationships import apply_scene_relationship_updates
from src.simulation.state.time_plan import (
    apply_time_updates,
    build_time_plan,
    commit_time_plan,
)


# Combined LLM workers for state classification, relationship deltas, and events.


async def _run_auxiliary_character_updates(
    actor_response: str,
    pc_id: str,
    scene_types: list[str] | None,
    participant_ids: list[str],
) -> None:
    """Run auxiliary multi-character extractors without blocking main state writes."""
    results = await asyncio.gather(
        apply_multi_character_state_updates(actor_response, pc_id, participant_ids=participant_ids),
        apply_multi_character_dynamic_information_updates(
            actor_response,
            pc_id,
            scene_types=scene_types,
            participant_ids=participant_ids,
        ),
        return_exceptions=True,
    )
    labels = ("multi-character state", "dynamic information")
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            print(f"[StateUpdater] auxiliary {label} update failed (ignored): {result}")


async def _run_state_update(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    world_config:   dict | None = None,
) -> dict:
    """
    Run the fast model for DynamicState extraction and relationship deltas.
    Event creation is handled by the separate event model.

    Returns: {dynamic_state, relationship_delta, ts_acceptance_delta?, northern_attachment_delta?}
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    ts_scoring = bool(world_config and world_config.get("ts_scoring_enabled"))

    ts_section = ""
    ts_json_fields = ""
    if ts_scoring:
        ts_section = f"""
## TS/North Acceptance Scoring (ONLY for NPC {npc_id})

ts_acceptance_delta = involuntary biological/physical feminine submission this turn.
DEFAULT 0. Increment only for:
  +1: Subtle involuntary reaction (caught breath, hesitation, warmth suppressed)
  +2: Clear involuntary feminine response she cannot deny
  +3: Significant biological defeat (menstrual pain forcing yield, arousal until she flees)
  +5: Major submission, shattering of male self. Extremely rare.
NEVER negative. Max +5 per turn.

northern_attachment_delta = genuine warmth/solidarity toward North characters this turn.
DEFAULT 0. Increment only for:
  +1: Small warmth moment from a North character
  +2: Genuine solidarity or recognition moment
  +3: Significant emotional shift. Rare.
NEVER negative. Max +3 per turn.
"""
        ts_json_fields = '\n  "ts_acceptance_delta": 0,\n  "northern_attachment_delta": 0,'

    system_instruction = f"""You are a precise state manager for a Korean roleplay system.
Analyze the scene. Return updates ONLY for Main NPC ({npc_id}) and the ({npc_id})<->{pc_id}) relationship.
CRITICAL: Do NOT assign secondary characters' injuries or emotions to {npc_id}."""

    prompt = f"""## Classification
LITERAL: Direct physical events: injury, illness, confirmed physical state, clothing description.
  Examples: "팔을 다쳤다" / "발목을 삐었다" / "코트를 걸쳤다" / "새 옷을 입었다"
FIGURATIVE: Emotional or metaphorical language. NEVER touch physical_condition / injury_detail.
  Examples: "심장이 찢어질 것 같아" / "죽고 싶다" / "머리가 터질 것 같아"

## DynamicState: extract ONLY actually changed fields
Always extractable:
- mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
- emotional_state: short Korean phrase reflecting inner feeling (e.g. "설렘", "불안", "안도", "긴장")
- mental_condition: stable/stressed/anxious/depressed/exhausted
- stress_level: JSON number from 0 to 10 ONLY. Never return strings like "high", "low", or "5".
  10=life-altering crisis or breakdown, 5=clear conflict or strong distress, 3=noticeable tension, 0=calm day.
- workplace_stress_level: JSON number from 0 to 10 ONLY. Never return strings like "high", "low", or "5".
  10=public professional disaster, 6=serious workplace pressure, 2=minor awkwardness, 0=no workplace stress.
- outfit: current clothing IF explicitly described (Korean, <= 50 chars). Omit entirely if not mentioned.
- injury_marks: "없음" or visible injury description. Only update if changed this scene.

LITERAL only:
- physical_condition: healthy/fatigued/injured/ill/hospitalized
- injury_detail: body part + type (LITERAL events only)

Rare growth events (omit entirely if not triggered this scene):
- age: JSON integer. Increment ONLY when narration explicitly confirms a birthday or year passing.
- circle_level: JSON integer. Update ONLY when a character explicitly achieves a new mana circle breakthrough.
- robe_grade: string. Update ONLY on confirmed Academy robe promotion. Values: "bronze"/"silver"/"gold"/"platinum"/"crimson".

Numeric fields must be JSON numbers, not quoted strings. Correct: {{"dynamic_state": {{"stress_level": 8}}}}. Wrong: {{"dynamic_state": {{"stress_level": "high"}}}}.

## Relationship delta
Integer (e.g. +5 or -10). null if unchanged.
- Default to null for routine talk, proximity, embarrassment, or unchanged rapport.
- Use -2..+2 for small but visible warmth, irritation, concern, or disappointment.
- Use -5..+5 only for a meaningful scene-level shift.
- Use -10..+10 only for rare relationship milestones: confession, betrayal, rescue, decisive reconciliation, near-breakup, or first intimacy.
- Do not use repeated mild intimacy, politeness, or compliance as automatic affinity growth.
{ts_section}
Return ONLY valid JSON:
{{
  "dynamic_state": {{}},{ts_json_fields}
  "relationship_delta": null
}}

Scene:
{actor_response[:2000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    try:
        response = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.0, "max_output_tokens": 1024,
                               "response_mime_type": "application/json"},
        )
    except TimeoutError:
        print("[StateUpdater] state_updater timeout")
        return {}

    plan = extract_json_from_llm(response.text, source="state_updater")
    return plan if isinstance(plan, dict) else {}


async def _run_event_creation(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
) -> dict:
    """
    Run the event model to decide whether the scene should become an Event.
    DynamicState and relationship deltas are not handled here.

    Returns: {new_event: null | {...}}
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    system_instruction = (
        f"You are a narrative archivist for a Korean roleplay simulation. "
        f"Decide whether this scene warrants creating a lasting Event record. "
        f"Focus on {npc_id} and {pc_id}."
    )

    prompt = f"""## Event Importance Scale
8-10: Major: hospitalization after accident, first confession, surgery, death, marriage
5-7: Significant: first meeting, major fight + real reconciliation, first intimacy, near-breakup, public humiliation
2-4: Minor but memorable: new injury encountered for the first time, new named character met, promise, secret revealed, gift/item exchanged, meaningful location transition, or small durable relationship beat
0-1: DO NOT create: routine chat, pure atmosphere, repeated follow-up, daily interaction with no lasting change

Create an Event if importance >= 2.
When uncertain between null and a minor durable beat, prefer an importance 2 Event.

## When creating an event
- id format: {{location}}_{{description}}_{{YYYYMMDD_HHMM}}
- summary: 1-2 sentence Korean narrative summary
- memory_type: one of: episodic (something that happened), emotional (a felt moment/shift), relational (relationship milestone)
- narrative_summary: 1 sentence, Actor-facing story continuity hook
- state_summary: 1 sentence, factual state/relationship preservation (who did what to whom)
- importance: integer 2-10
- impact: brief phrase describing the lasting effect

When importance >= 7, also add:
- new_relationship_status: 1-2 English sentences describing the relationship state AFTER this event, specific about what shifted

Return ONLY valid JSON:
{{
  "new_event": null
}}

Scene:
{actor_response[:2000]}"""

    model = get_model(model_name=EVENT_MODEL, system_prompt=system_instruction)
    try:
        response = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.0, "max_output_tokens": 1024,
                               "response_mime_type": "application/json"},
        )
    except TimeoutError:
        print("[StateUpdater] event_creator timeout")
        return {}

    plan = extract_json_from_llm(response.text, source="event_creator")
    return plan if isinstance(plan, dict) else {}


async def _run_combined_update(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    world_config:   dict | None = None,
) -> dict:
    """
    Run both LLM workers concurrently:
      - Flash (COMPLEX_MODEL): DynamicState extraction + relationship delta
      - Pro (EVENT_MODEL): event creation decision

    Returns:
        {dynamic_state, relationship_delta, new_event,
         ts_acceptance_delta?, northern_attachment_delta?}
    """
    state_result, event_result = await asyncio.gather(
        _run_state_update(actor_response, npc_id, pc_id, world_config),
        _run_event_creation(actor_response, npc_id, pc_id),
        return_exceptions=True,
    )
    if isinstance(state_result, Exception):
        print(f"[StateUpdater] state update worker failed (ignored): {state_result}")
        state_plan = {}
    else:
        state_plan = state_result
    if isinstance(event_result, Exception):
        print(f"[StateUpdater] event creation worker failed (ignored): {event_result}")
        event_plan = {}
    else:
        event_plan = event_result
    return {**state_plan, "new_event": event_plan.get("new_event")}


_EVENT_CREATION_SIGNAL_RE = re.compile(
    r"(처음|만났|마주쳤|소개|약속|비밀|고백|선물|건넸|받았|다툼|싸움|갈등|"
    r"떠났|도착|이동|장소|병원|다쳤|부상|키스|관계|소문|공개|"
    r"first|met|promise|secret|confession|gift|fight|arrived|left|hospital)",
    re.IGNORECASE,
)


def _has_event_creation_signal(actor_response: str, participant_ids: list[str]) -> bool:
    """Return whether a low-state-change scene is still worth asking the Event model about."""
    text = actor_response.strip()
    if not text:
        return False
    if len(participant_ids) > 2:
        return True
    return bool(_EVENT_CREATION_SIGNAL_RE.search(text))


# State update main path


async def process_actor_response(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    scene_types:    list[str] | None = None,
    scene_chars:    list[str] | None = None,
    world_config:   dict | None = None,
    manager_effects: dict | None = None,
) -> str | None:
    """
    Analyze an accepted Actor response and apply persistent state side effects.
    Returns an OOC pregnancy/organic system message if one is generated, otherwise None.
    """
    from src.simulation.systems.social import ensure_scene_relationships, resolve_and_update as wb_resolve

    guard = guard_actor_response(actor_response, npc_id, pc_id, world_config)
    feasibility_audit = _audit_time_location_schedule(manager_effects)
    if not guard["passed"]:
        print(f"[StateGuard] rejected: {json.dumps(guard, ensure_ascii=False)}")
        _write_state_audit_snapshot(
            actor_response,
            npc_id,
            pc_id,
            guard,
            feasibility_audit=feasibility_audit,
        )
        return None
    if guard["issues"]:
        print(f"[StateGuard] warning: {json.dumps(guard, ensure_ascii=False)}")

    participant_ids = [pc_id, npc_id]
    if world_config and scene_chars:
        try:
            resolved_ids = await wb_resolve(scene_chars, npc_id, pc_id, world_config)
            participant_ids = [pc_id, npc_id, *resolved_ids]
        except Exception as e:
            print(f"[WorldBuilder] early resolve failed (ignored): {e}")

    if npc_id != pc_id:
        try:
            await ensure_scene_relationships(participant_ids)
        except Exception as e:
            print(f"[RelationshipUpdater] primary relationship ensure failed (ignored): {e}")

    if scene_types and not _needs_classification(actor_response, scene_types):
        print("[StateUpdater] skipped (no state-relevant change)")
        # 두 추출 작업은 서로 독립적이므로 병렬 실행
        await _run_auxiliary_character_updates(
            actor_response,
            pc_id,
            scene_types,
            participant_ids,
        )
        if len(participant_ids) > 2:
            await apply_scene_relationship_updates(
                actor_response,
                participant_ids,
                primary_pair=(npc_id, pc_id),
            )
        if npc_id != pc_id and _has_event_creation_signal(actor_response, participant_ids):
            try:
                event_plan = await _run_event_creation(actor_response, npc_id, pc_id)
                new_event, event_candidate = _audit_event_candidate(event_plan.get("new_event"), actor_response)
                if event_candidate:
                    print(f"[EventDiff] {json.dumps(event_candidate, ensure_ascii=False)}")
                _write_state_audit_snapshot(
                    actor_response=actor_response,
                    npc_id=npc_id,
                    pc_id=pc_id,
                    guard=guard,
                    event_candidate=event_candidate,
                    feasibility_audit=feasibility_audit,
                )
                if new_event:
                    await _create_event(new_event, npc_id, pc_id)
            except Exception as e:
                print(f"[StateUpdater] low-change event creation failed (ignored): {e}")
        return None

    if npc_id == pc_id:
        # NPC=PC self-state path: update DynamicState only and skip relationship/event/gossip.
        # multi_character 와 dynamic_info 는 독립적이므로 병렬 실행
        print("[StateUpdater] NPC=PC: DynamicState update only")
        await _run_auxiliary_character_updates(
            actor_response,
            pc_id,
            scene_types,
            participant_ids,
        )
        try:
            state_plan = await _run_state_update(actor_response, npc_id, pc_id, world_config)
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
        if len(participant_ids) > 2:
            try:
                await apply_scene_relationship_updates(
                    actor_response,
                    participant_ids,
                    primary_pair=None,
                )
            except Exception as e:
                print(f"[WorldBuilder] resolve failed (continuing): {e}")
        try:
            return await process_ejaculation(npc_id, actor_response, scene_char_ids=scene_chars)
        except Exception as e:
            print(f"[PregnancyMgr] processing failed (continuing): {e}")
        return None

    # multi_character / dynamic_info / combined_update 는 서로 독립적이므로 병렬 실행
    auxiliary_task = asyncio.create_task(_run_auxiliary_character_updates(
        actor_response,
        pc_id,
        scene_types,
        participant_ids,
    ))
    try:
        plan = await _run_combined_update(actor_response, npc_id, pc_id, world_config)
    except Exception as e:
        print(f"[StateUpdater] combined update failed (ignored): {e}")
        await auxiliary_task
        return None
    await auxiliary_task
    if not plan:
        return None

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
            print(f"[StateDiff] {json.dumps(state_candidates, ensure_ascii=False)}")
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
            print(f"[RelationshipDiff] {json.dumps(rel_candidate, ensure_ascii=False)}")
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

    # Create Event and update relationship status when importance is high enough.
    try:
        new_event, event_candidate = _audit_event_candidate(plan.get("new_event"), actor_response)
        if event_candidate:
            print(f"[EventDiff] {json.dumps(event_candidate, ensure_ascii=False)}")
        _write_state_audit_snapshot(
            actor_response          = actor_response,
            npc_id                  = npc_id,
            pc_id                   = pc_id,
            guard                   = guard,
            state_candidates        = state_candidates,
            relationship_candidate  = rel_candidate,
            event_candidate         = event_candidate,
            feasibility_audit       = feasibility_audit,
        )
        if new_event:
            await _create_event(new_event, npc_id, pc_id)
            new_status = new_event.get("new_relationship_status")
            event_importance = _safe_int(new_event.get("importance"), 0)
            if new_status and event_importance >= 7:
                await _apply_relationship_status(npc_id, pc_id, new_status)
            if event_importance >= 6:
                asyncio.create_task(update_relationship_narrative(
                    npc_id, pc_id, new_event.get("summary", ""), event_importance,
                ))
    except Exception as e:
        print(f"[StateUpdater] event creation failed (ignored): {e}")

    # Apply non-primary participant relationship updates.
    if len(participant_ids) > 2:
        try:
            await apply_scene_relationship_updates(
                actor_response,
                participant_ids,
                primary_pair=(npc_id, pc_id),
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve failed (ignored): {e}")

    # Life-depth side effects use event importance and relationship delta.
    # Gossip, memory distortion, and personality drift are intentionally best-effort.
    _d        = int(delta or 0)
    _imp      = _safe_int(new_event.get("importance"), 0) if new_event else 0
    _depth_ts = await _get_current_iso_time()
    _depth_dt = datetime.fromisoformat(_depth_ts)

    # Gossip propagation: important event plus meaningful affinity movement.
    if new_event and _imp >= 5 and abs(_d) >= 3:
        try:
            from src.simulation.systems.reputation import propagate_gossip
            await propagate_gossip(
                event_summary      = new_event.get("summary", ""),
                event_importance   = _imp,
                relationship_delta = _d,
                source_npc_id      = npc_id,
                pc_id              = pc_id,
                timestamp_iso      = _depth_ts,
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
    try:
        from src.simulation.systems.personality import check_personality_drift
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

    try:
        return await process_ejaculation(npc_id, actor_response, scene_char_ids=scene_chars)
    except Exception as e:
        print(f"[PregnancyMgr] processing failed (ignored): {e}")
    return None
