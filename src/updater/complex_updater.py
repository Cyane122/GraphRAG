"""
Complex event handler.
LLM으로 멀티 노드 업데이트 플랜 생성 → DynamicState 갱신 + 호감도 delta + Event 노드 생성.
Event 생성 시 summary를 임베딩해 Vector Index에 등록.
Event 생성 후 관련 캐릭터별 Memory 노드도 자동 생성.
"""

import os
import json
from datetime import datetime

from src.utils.llm_utils import async_llm_client, extract_json_from_llm
from src.utils.db_utils import (
    async_driver,
    update_dynamic_state,
    update_relationship_affinity,
    get_in_universe_time,
)
from src.utils.embedder import embed_async
from src.memory.decay_manager import ensure_memories_for_event
from src.world.world_builder import resolve_and_update as wb_resolve

COMPLEX_MODEL = os.getenv("MODEL_COMPLEX_UPDATER", "claude-sonnet-4-6")


async def _get_current_iso_time() -> str:
    from src.utils.db_utils import async_driver as _ad
    async with _ad.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await rec.single()
        if row and row["ct"]:
            return row["ct"]
    return datetime.now().isoformat()


# ════════════════════════════════════════════════════════════
# LLM — 업데이트 플랜 생성
# ════════════════════════════════════════════════════════════

async def _generate_update_plan(
    actor_response:  str,
    npc_id:          str,
    pc_id:           str,
    initial_changes: dict,
) -> dict:
    prompt = f"""You are a precise game state manager for a roleplay system.
Analyze this roleplay scene and produce a structured update plan ONLY for the MAIN NPC ({npc_id}) and the relationship between ({npc_id}) and ({pc_id}).

## Context
NPC: {npc_id}, PC: {pc_id}
Initial changes detected: {json.dumps(initial_changes, ensure_ascii=False)}

## [CRITICAL ANTI-CONFUSION RULE]
- Distinguish Main NPC ({npc_id}) from secondary characters.
- DO NOT assign secondary characters' injuries/emotions to the Main NPC.
- Only update {npc_id}'s state based on what ACTUALLY happened to {npc_id}.

## Tasks
1. Update DynamicState fields as needed (physical/mental/mood/stress/location)
2. Return relationship affinity delta as integer (e.g. +5 or -10), null if unchanged
3. Create an Event node only if the scene meets the criteria below

## Event Importance Scale (0–10)

9–10 — Permanent. Life-altering only.
  Examples: marriage proposal / surgery-level injury / first confession / overcoming burnout / serious accident

6–8 — Slow decay. Significant.
  Examples: first meeting / major fight + genuine reconciliation / first physical intimacy / near-breakup

3–5 — Medium decay. Noteworthy.
  Examples: mild injury + first clinic visit / first meeting new character / minor argument with lasting tension

0–2 — DO NOT create an event.
  Examples: meal / convenience store / short chat / routine day / follow-up visit (injury already recorded)

## Calibration
CREATE (9): {npc_id} hospitalized after car accident
CREATE (8): Genuine reconciliation after long emotional burnout
CREATE (5): {npc_id} visits orthopedic clinic for first time due to back injury
CREATE (4): Minor argument leads to cold war
CREATE (3): First time meeting {npc_id}
NO EVENT: They had dinner together
NO EVENT: {npc_id} got annoyed but recovered quickly
NO EVENT: Follow-up visit when injury already recorded

## Event ID Format
{{location}}_{{description}}_{{YYYYMMDD_HHMM}}

Return ONLY a JSON object:
{{
  "dynamic_state": {{}},
  "relationship_delta": null,
  "new_event": null
}}

If creating an event, replace new_event with:
{{
  "id": "string",
  "summary": "1–2 sentence Korean summary",
  "importance": 0,
  "impact": "brief description"
}}

Roleplay scene:
{actor_response[:2000]}"""

    response = await async_llm_client.messages.create(
        model=COMPLEX_MODEL,
        max_tokens=1024,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )

    plan = extract_json_from_llm(response.content[0].text)
    if not plan:
        plan = {"dynamic_state": {}, "relationship_delta": None, "new_event": None}
    return plan


# ════════════════════════════════════════════════════════════
# DB 작업
# ════════════════════════════════════════════════════════════

async def _create_event(event_data: dict, npc_id: str, pc_id: str) -> None:
    if not event_data or not event_data.get("id"):
        return

    timestamp_fmt = await get_in_universe_time()
    timestamp_iso = await _get_current_iso_time()
    summary       = event_data.get("summary", "")
    importance    = event_data.get("importance", 5)

    embedding = None
    if summary:
        try:
            embedding = await embed_async(summary)
        except Exception as e:
            print(f"[ComplexUpdater] 임베딩 생성 실패 (무시): {e}")

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Event {
                id:                $id,
                summary:           $summary,
                timestamp:         $timestamp,
                importance:        $importance,
                impact:            $impact,
                decay_rate:        0.1,
                summary_level:     0,
                safety_impact:     0.0,
                safety_resolved:   true,
                safety_decay_rate: 0.0
            })
        """,
            id=event_data["id"],
            summary=summary,
            timestamp=timestamp_fmt,
            importance=importance,
            impact=event_data.get("impact", ""),
        )

        for char_id in [npc_id, pc_id]:
            await session.run("""
                MATCH (c:Character {id: $char_id})
                MATCH (e:Event {id: $event_id})
                CREATE (c)-[:INVOLVED_IN]->(e)
            """, char_id=char_id, event_id=event_data["id"])

        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.shared_events    = coalesce(r.shared_events, []) + [$event_id],
                r.last_interaction = $timestamp
        """, a=npc_id, b=pc_id, event_id=event_data["id"], timestamp=timestamp_fmt)

    print(f"[ComplexUpdater] event created: {event_data['id']} (importance={importance})")

    await ensure_memories_for_event(
        event_id   = event_data["id"],
        summary    = summary,
        importance = importance,
        char_ids   = [npc_id, pc_id],
        timestamp  = timestamp_iso,
        embedding  = embedding,
    )


async def _evolve_relationship_status(
    char_a:           str,
    char_b:           str,
    event_summary:    str,
    event_importance: int,
) -> None:
    """importance ≥ 7인 이벤트 발생 시 RELATIONSHIP의 current_status를 재작성."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN r.current_status AS status, r.type AS type,
                   r.affinity AS affinity, r.trust AS trust
        """, a=char_a, b=char_b)
        row = await rec.single()
        if not row:
            return
        current = dict(row)

    prompt = f"""You are a relationship narrator for a roleplay system.
A significant event (importance {event_importance}/10) just occurred between two characters.
Rewrite the relationship's current_status to reflect this change.

## Current relationship
type: {current.get('type')}
affinity: {current.get('affinity')} / trust: {current.get('trust')}
current_status: {current.get('status')}

## Event that just occurred
{event_summary}

## Rules
- Write in English, 1–3 sentences max.
- Describe the *current* state of the relationship after this event.
- Be specific about what shifted. Do not copy the old status verbatim.

Return ONLY the new current_status string. No quotes, no JSON, no explanation."""

    response = await async_llm_client.messages.create(
        model=COMPLEX_MODEL,
        max_tokens=256,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    new_status = response.content[0].text.strip()

    async with async_driver.session() as session:
        for a, b in [(char_a, char_b), (char_b, char_a)]:
            await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                SET r.current_status = $status
            """, a=a, b=b, status=new_status)

    print(f"[ComplexUpdater] relationship status updated: {char_a} ↔ {char_b}")


# ════════════════════════════════════════════════════════════
# 공개 진입점
# ════════════════════════════════════════════════════════════

async def delegate_complex_update(
    actor_response:  str,
    npc_id:          str,
    pc_id:           str,
    initial_changes: dict | None = None,
    event_only:      bool = False,
    world_config:    dict | None = None,
    scene_chars:     list[str] | None = None,
) -> None:
    """
    Complex 업데이트 파이프라인.
    event_only=True → DynamicState/호감도 없이 Event 생성만.
    scene_chars: CoT 파싱 결과 → world_builder로 전달.
    """
    plan = await _generate_update_plan(
        actor_response  = actor_response,
        npc_id          = npc_id,
        pc_id           = pc_id,
        initial_changes = initial_changes or {},
    )

    if not event_only:
        state_updates = plan.get("dynamic_state", {})
        if state_updates:
            await update_dynamic_state(npc_id, state_updates)

        delta = plan.get("relationship_delta")
        if delta:
            await update_relationship_affinity(npc_id, pc_id, int(delta))
            await update_relationship_affinity(pc_id, npc_id, int(delta))

    new_event = plan.get("new_event")
    if new_event:
        await _create_event(new_event, npc_id, pc_id)
        if new_event.get("importance", 0) >= 7:
            await _evolve_relationship_status(
                npc_id, pc_id,
                new_event.get("summary", ""),
                new_event.get("importance", 7),
            )

    if world_config and scene_chars:
        try:
            await wb_resolve(
                char_names       = scene_chars,
                main_npc_id      = npc_id,
                pc_id            = pc_id,
                world_config     = world_config,
                event_id         = new_event.get("id") if new_event else None,
                event_importance = new_event.get("importance", 0) if new_event else 0,
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")