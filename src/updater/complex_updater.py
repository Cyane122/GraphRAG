# src/updater/complex_updater.py
"""
Complex event handler — delegates to LLM for multi-node updates,
event node creation, and RELATIONSHIP shared_events update.
"""

import os
import json
from datetime import datetime

# 공통 유틸리티 Import
from src.utils.llm_utils import llm_client, extract_json_from_llm
from src.utils.db_utils import async_driver, update_dynamic_state, update_relationship_affinity, get_in_universe_time

COMPLEX_MODEL = os.getenv("MODEL_COMPLEX_UPDATER", "claude-sonnet-4-6")


# ════════════════════════════════════════════════════════════
# LLM — generate update plan
# ════════════════════════════════════════════════════════════

def _generate_update_plan(
    actor_response: str,
    npc_id: str,
    pc_id: str,
    initial_changes: dict,
) -> dict:
    prompt = f"""You are a precise game state manager for a roleplay system.
Analyze this roleplay scene and produce a structured update plan ONLY for the MAIN NPC ({npc_id}) and the relationship between ({npc_id}) and ({pc_id}).

## Context
NPC: {npc_id}, PC: {pc_id}
Initial changes detected: {json.dumps(initial_changes, ensure_ascii=False)}

## [CRITICAL ANTI-CONFUSION RULE]
- You MUST distinguish between the Main NPC ({npc_id}) and other secondary characters (like friends, coworkers, passing strangers).
- DO NOT extract injuries, emotions, or life events of secondary characters and assign them to the Main NPC.
- For example, if a friend broke their ankle, DO NOT update {npc_id}'s physical_condition.
- Only update {npc_id}'s state based on what ACTUALLY happened to {npc_id}.

## Tasks
1. Update DynamicState fields as needed (physical/mental/mood/stress/location)
2. Return relationship affinity delta as integer (e.g. +5 or -10), null if unchanged
3. Create an Event node only if the scene meets the criteria below

## Event Importance Scale (0–10)

9–10 — Permanent, no decay. Life-altering only.
  Examples: marriage proposal / surgery-level injury / first confession / overcoming relationship burnout / serious accident

6–8 — Slow decay. Significant but not permanent.
  Examples: first meeting / major fight then genuine reconciliation / first physical intimacy / near-breakup

3–5 — Medium decay. Noteworthy but minor.
  Examples: mild injury + first clinic visit for it / first meeting with a new character / minor argument with lasting tension

0–2 — DO NOT create an event.
  Examples: meal / convenience store / short chat / routine day / follow-up visit when injury already recorded

## Calibration
CREATE (9): {npc_id} hospitalized after car accident
CREATE (8): Genuine reconciliation after long emotional burnout
CREATE (5): {npc_id} visits orthopedic clinic for first time due to back injury
CREATE (4): Minor argument leads to a cold war between them
CREATE (3): First time meeting {npc_id}
NO EVENT: They had dinner together
NO EVENT: {npc_id} got annoyed but recovered quickly
NO EVENT: Follow-up clinic visit when injury is already in the record

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

    response = llm_client.messages.create(
        model=COMPLEX_MODEL,
        max_tokens=1024,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )

    plan = extract_json_from_llm(response.content[0].text)
    if not plan:
        plan = {"dynamic_state": {}, "relationship_delta": None, "new_event": None}
    return plan


async def _create_event(event_data: dict, npc_id: str, pc_id: str) -> None:
    if not event_data or not event_data.get("id"):
        return
    timestamp = await get_in_universe_time()
    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Event {
                id:            $id,
                summary:       $summary,
                timestamp:     $timestamp,
                importance:    $importance,
                impact:        $impact,
                decay_rate:    0.1,
                summary_level: 0
            })
        """,
            id=event_data["id"],
            summary=event_data.get("summary", ""),
            timestamp=timestamp,
            importance=event_data.get("importance", 5),
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
            SET r.shared_events = coalesce(r.shared_events, []) + [$event_id],
                r.last_interaction = $timestamp
        """, a=npc_id, b=pc_id, event_id=event_data["id"], timestamp=timestamp)

    print(f"[ComplexUpdater] event created: {event_data['id']}")


async def _evolve_relationship_status(
    char_a: str,
    char_b: str,
    event_summary: str,
    event_importance: int,
) -> None:
    """
    importance ≥ 7인 이벤트 발생 시 RELATIONSHIP의 current_status를 Haiku로 재작성.
    양방향 모두 업데이트.
    """
    # 현재 관계 상태 조회
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
- If this is a breakup/betrayal → reflect distance/fracture.
- If this is a reconciliation/milestone → reflect deepened bond.

Return ONLY the new current_status string. No quotes, no JSON, no explanation."""

    response = llm_client.messages.create(
        model=COMPLEX_MODEL,
        max_tokens=200,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    new_status = response.content[0].text.strip().strip('"')

    # 양방향 업데이트
    async with async_driver.session() as session:
        for a, b in [(char_a, char_b), (char_b, char_a)]:
            await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                SET r.current_status = $status
            """, a=a, b=b, status=new_status)

    print(f"[ComplexUpdater] relationship evolved: {char_a}↔{char_b} → {new_status[:60]}...")


# ════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════

async def delegate_complex_update(
    actor_response: str,
    npc_id: str,
    pc_id: str,
    initial_changes: dict,
    event_only: bool = False,
) -> None:
    print("[ComplexUpdater] processing...")

    plan = _generate_update_plan(actor_response, npc_id, pc_id, initial_changes)
    print(f"[ComplexUpdater] plan: {json.dumps(plan, ensure_ascii=False)}")

    if not event_only:
        await update_dynamic_state(npc_id, plan.get("dynamic_state", {}))

        delta = plan.get("relationship_delta")
        if delta is not None:
            await update_relationship_affinity(npc_id, pc_id, int(delta))
            await update_relationship_affinity(pc_id, npc_id, int(delta))

    # 이벤트 ID의 YYYYMMDD_HHMM 리터럴 → 실제 타임스탬프로 치환
    raw_event = plan.get("new_event")
    if raw_event and isinstance(raw_event, dict):
        event_id = raw_event.get("id") or raw_event.get("event_id")
        if event_id:
            ts = await get_in_universe_time()
            event_id = event_id.replace("YYYYMMDD_HHMM", ts)
            created_event = {
                "id":         event_id,
                "summary":    raw_event.get("summary") or raw_event.get("description", ""),
                "importance": raw_event.get("importance") or (
                    9 if raw_event.get("significance") == "high"
                    else 6 if raw_event.get("significance") == "medium"
                    else 3
                ),
                "impact": str(raw_event.get("impact") or raw_event.get("relationship_impact", "")),
            }
            await _create_event(created_event, npc_id, pc_id)

            # importance ≥ 7 → 관계 서사 진화
            if created_event["importance"] >= 7:
                await _evolve_relationship_status(
                    npc_id, pc_id,
                    created_event["summary"],
                    created_event["importance"],
                )

    print("[ComplexUpdater] done")