"""
멀티 노드 업데이트 및 이벤트 노드 생성 담당.
LLM으로 업데이트 플랜 생성 → DynamicState 갱신 + 호감도 delta + Memory 노드 생성.
outfit / injury_marks 필드를 업데이트 플랜에 포함해 묘사 일관성 버퍼를 지원한다.
importance ≥ 7 이벤트 발생 시 RELATIONSHIP.current_status 양방향 재작성.
world_config["ts_scoring_enabled"]=True 시 ts_acceptance / northern_attachment delta 갱신.
"""

import os
import json
from datetime import datetime

from src.utils.llm_utils import get_model, extract_json_from_llm
from src.utils.db_utils import (
    async_driver,
    update_dynamic_state,
    update_relationship_affinity,
    get_in_universe_time,
)
from src.utils.embedder import embed_async
from src.memory.decay_manager import ensure_memories_for_event
from src.world.world_builder import resolve_and_update as wb_resolve
from src.updater.pregnancy_manager import process_ejaculation

COMPLEX_MODEL = os.getenv("MODEL_COMPLEX_UPDATER", "gemini-3-flash-preview")

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
    world_config:    dict | None = None,
) -> dict:
    system_instruction = f"""You are a precise game state manager for a roleplay system.
Analyze this roleplay scene and produce a structured update plan ONLY for the MAIN NPC ({npc_id}) and the relationship between ({npc_id}) and ({pc_id}).

## [CRITICAL ANTI-CONFUSION RULE]
- Distinguish Main NPC ({npc_id}) from secondary characters.
- DO NOT assign secondary characters' injuries/emotions to the Main NPC.
- Only update {npc_id}'s state based on what ACTUALLY happened to {npc_id}.
"""

    ts_scoring_enabled = bool(world_config and world_config.get("ts_scoring_enabled"))

    ts_task_section = ""
    if ts_scoring_enabled:
        ts_task_section = f"""
4. Evaluate TS/North acceptance deltas (ONLY when NPC is {npc_id}):

   ts_acceptance_delta — how much did Arsien's male ego lose ground to her female body this turn?
   DEFAULT IS 0. Increment ONLY when the scene contains involuntary physical/biological submission.
   Scale:
     0  → No feminine submission occurred. (Most turns — default.)
     +1 → Subtle involuntary reaction: a faint caught breath, a hesitation, warmth noticed then suppressed.
     +2 → Clear involuntary feminine response she cannot deny to herself (involuntary moan suppressed,
           grip failing on a familiar weapon, cringing from aura she would have laughed at before).
     +3 → Significant biological defeat: menstrual pain forcing her to yield physically,
           chest binding broken under breathlessness, body arousing against her will until she physically flees.
     +5 → Major submission. Extremely rare. Reserve for scenes where the male self is visibly shattered
           (first fully conscious sexual response, collapse under biological pain in front of others).
   NEVER negative. Maximum +5 per turn. Accumulates across many sessions before reaching high stages.

   northern_attachment_delta — how much did the North stop feeling like exile this turn?
   DEFAULT IS 0. Increment ONLY when Arsien genuinely felt warmth, solidarity, or recognition from a North character.
   Scale:
     0  → No emotional North connection occurred. (Most turns — default.)
     +1 → Small warmth moment: Eleanor's gesture, Marcus's competence observed, Essila's stubbornness respected.
     +2 → Genuine solidarity or recognition: Karno family treating her word as valid, Sian's outcast moment
           mirroring her own, realizing Elencia's letter was pure tool-usage.
     +3 → Significant emotional shift. Rare. (e.g., first time she genuinely defends a Karno person without
           being asked, or receives care she has never had from family.)
   NEVER negative. Maximum +3 per turn.

   CALIBRATION EXAMPLES:
   Arsien ignores Sian and stares out the window  → ts_delta=0, north_delta=0
   Arsien's wrist buckles slightly lifting a sword → ts_delta=1, north_delta=0
   Arsien's moan half-escapes before she bites it  → ts_delta=2, north_delta=0
   Arsien collapses in menstrual pain alone         → ts_delta=3, north_delta=0
   Eleanor wraps a fur scarf around Arsien          → ts_delta=0, north_delta=1
   Arsien notices the letter from Elencia is orders → ts_delta=0, north_delta=2
   Arsien defends Sian at the dinner table          → ts_delta=0, north_delta=2
   First conscious arousal response to Sian's touch → ts_delta=5, north_delta=0
"""

    ts_json_fields = ""
    if ts_scoring_enabled:
        ts_json_fields = '\n  "ts_acceptance_delta": 0,\n  "northern_attachment_delta": 0,'

    prompt = f"""## Context
NPC: {npc_id}, PC: {pc_id}
Initial changes detected: {json.dumps(initial_changes, ensure_ascii=False)}

## Tasks
1. Update DynamicState fields as needed — only include fields that ACTUALLY changed:
   - physical_condition: healthy/fatigued/injured/ill/hospitalized
   - mental_condition: stable/stressed/anxious/depressed/exhausted
   - mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
   - stress_level: 0–10 integer
   - workplace_stress_level: 0–10 integer
   - outfit: current clothing IF explicitly described (Korean, ≤25 chars). Omit if not mentioned.
     e.g. "청바지 + 흰 니트" / "운동복 차림" / "수면 반바지 + 민소매"
   - injury_marks: "없음" OR visible injury description IF changed this scene. Omit if unchanged.
     e.g. "오른 발목 부상" / "팔 찰과상" / "없음" (when healed)
   - injury_detail: body part + type (LITERAL physical only)
2. Return relationship affinity delta as integer (e.g. +5 or -10), null if unchanged
3. Create an Event node only if the scene meets the criteria below
{ts_task_section}

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
  "dynamic_state": {{}},{ts_json_fields}
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

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)

    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0}
    )

    plan = extract_json_from_llm(response.text, source="complex_updater")
    if not plan:
        plan = {"dynamic_state": {}, "relationship_delta": None, "new_event": None}
    return plan


# ════════════════════════════════════════════════════════════
# TS / 북부 수치 갱신
# ════════════════════════════════════════════════════════════

async def _update_acceptance_scores(
    npc_id:   str,
    ts_delta: int,
    na_delta: int,
) -> None:
    """
    ts_acceptance / northern_attachment 수치를 Neo4j에서 읽어 delta를 더하고 저장한다.
    두 필드가 DynamicState에 존재하는 경우에만 실행 (WHERE 절로 보호).
    텍스트 필드(body_perception, behavioral_facade)는 건드리지 않는다.
    rofan.py의 get_full_config_async()가 수치를 읽어 프롬프트 주입 시 실시간으로 매핑한다.
    """
    if not ts_delta and not na_delta:
        return

    async with async_driver.session() as session:
        await session.run(
            """
            MATCH (c:Character {id: $npc_id})-[:HAS_STATE]->(s:DynamicState)
            WHERE s.ts_acceptance IS NOT NULL
            SET s.ts_acceptance       = max(0, min(100,
                    coalesce(s.ts_acceptance, 0) + $ts_delta)),
                s.northern_attachment = max(0, min(100,
                    coalesce(s.northern_attachment, 0) + $na_delta))
            """,
            npc_id   = npc_id,
            ts_delta = ts_delta,
            na_delta = na_delta,
        )

    if ts_delta:
        print(f"[AcceptanceUpdater] {npc_id}: ts_acceptance +{ts_delta}")
    if na_delta:
        print(f"[AcceptanceUpdater] {npc_id}: northern_attachment +{na_delta}")


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

    system_instruction = "You are a relationship narrator for a roleplay system."

    prompt = f"""A significant event (importance {event_importance}/10) just occurred between two characters.
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

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)

    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0}
    )
    new_status = response.text.strip()

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
) -> str | None:
    """
    Complex 업데이트 파이프라인.
    event_only=True → DynamicState/호감도 없이 Event 생성만.
    scene_chars: CoT 파싱 결과 → world_builder로 전달.
    world_config["ts_scoring_enabled"]=True → ts_acceptance / northern_attachment delta 갱신.
    """
    plan = await _generate_update_plan(
        actor_response  = actor_response,
        npc_id          = npc_id,
        pc_id           = pc_id,
        initial_changes = initial_changes or {},
        world_config    = world_config,
    )

    if not event_only:
        state_updates = plan.get("dynamic_state", {})
        if state_updates:
            await update_dynamic_state(npc_id, state_updates)

        delta = plan.get("relationship_delta")
        if delta:
            await update_relationship_affinity(npc_id, pc_id, int(delta))
            await update_relationship_affinity(pc_id, npc_id, int(delta))

        # ── TS / 북부 수치 갱신 ──────────────────────────────
        if world_config and world_config.get("ts_scoring_enabled"):
            ts_delta = int(plan.get("ts_acceptance_delta") or 0)
            na_delta = int(plan.get("northern_attachment_delta") or 0)
            await _update_acceptance_scores(npc_id, ts_delta, na_delta)

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

    # 질내사정 감지 → 임신 확률 계산
    try:
        ooc_msg = await process_ejaculation(npc_id, actor_response)
        return ooc_msg
    except Exception as e:
        print(f"[PregnancyMgr] 처리 실패 (무시): {e}")
    return None