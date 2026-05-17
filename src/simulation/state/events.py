# ================================
# src/simulation/state/events.py
#
# Create Event records and handle event-only complex updates.
#
# Functions
#   - _create_event(event_data: dict, npc_id: str, pc_id: str) -> None : Create Event and Memory nodes
#   - _update_acceptance_scores(npc_id: str, ts_delta: int, na_delta: int) -> None : Apply TS/NA deltas
#   - _get_current_iso_time() -> str : Fetch current game time as an ISO string
#   - delegate_complex_update(actor_response: str, npc_id: str, pc_id: str, initial_changes: dict | None, event_only: bool, world_config: dict | None, scene_chars: list[str] | None) -> None : Delegate complex updates including event-only mode
# ================================
import json
import re
from datetime import datetime

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL, MODEL_EVENT_CREATOR as NARRATIVE_MODEL
from src.core.database import async_driver, get_in_universe_time
from src.core.embedding.encoder import embed_async
from src.simulation.systems.memory import ensure_memories_for_event
from src.simulation.state.audit import _prepare_event_summaries

async def _apply_relationship_status(char_a: str, char_b: str, new_status: str) -> None:
    """RELATIONSHIP 양방향 current_status를 갱신한다."""
    async with async_driver.session() as session:
        for a, b in [(char_a, char_b), (char_b, char_a)]:
            await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                SET r.current_status = $status
            """, a=a, b=b, status=new_status)
    print(f"[Updater] relationship status updated: {char_a} ↔ {char_b}")

# ════════════════════════════════════════════════════════════
# event_only 경로 (OOC 트리거 전용)
# ════════════════════════════════════════════════════════════

async def _get_current_iso_time() -> str:
    """GlobalState에서 현재 ISO 시간 문자열을 반환한다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await rec.single()
        if row and row["ct"]:
            return row["ct"]
    return datetime.now().isoformat()


async def _generate_event_plan(
    input_text:      str,
    npc_id:          str,
    pc_id:           str,
    initial_changes: dict,
) -> dict:
    """
    event_only 경로용 LLM 플랜 생성.
    DynamicState / 호감도 없이 Event 생성 여부와 relationship_status만 판단한다.
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    system_instruction = f"You are a precise event recorder for a roleplay system. Focus on {npc_id} and {pc_id}."

    prompt = f"""Initial state changes detected: {json.dumps(initial_changes, ensure_ascii=False)}

Decide whether to create an Event node based on the scene below.

## Event Importance
8-10: Major (hospitalization, surgery, serious accident, first confession)
5-7: Significant (major injury, near-breakup, first intimacy, public humiliation)
2-4: Minor but memorable (first clinic visit for new injury, new named character, promise, secret, gift/item exchange, meaningful location transition, small durable conflict)
0-1: DO NOT create (routine, pure atmosphere, repeated follow-up)

Create an Event if importance >= 2. When uncertain between null and a minor durable beat, prefer importance 2.

ID format: {{location}}_{{description}}_{{YYYYMMDD_HHMM}}

When importance >= 7 add "new_relationship_status" to new_event:
  1-3 sentences English describing the current relationship state after this event.

When creating new_event include:
- memory_type: one of episodic, emotional, relational.
- narrative_summary: Actor-facing story continuity, 1 sentence.
- state_summary: factual state/relationship preservation, 1 sentence.

Return ONLY valid JSON:
{{
  "new_event": null
}}

Scene / OOC command:
{input_text[:1500]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
    )

    plan = extract_json_from_llm(response.text, source="event_plan")
    if not isinstance(plan, dict):
        plan = {}
    return plan


async def _update_acceptance_scores(npc_id: str, ts_delta: int, na_delta: int) -> None:
    """ts_acceptance / northern_attachment 수치를 delta 적용 후 저장."""
    if not ts_delta and not na_delta:
        return

    async with async_driver.session() as session:
        await session.run(
            """
            MATCH (c:Character {id: $npc_id})-[:HAS_STATE]->(s:DynamicState)
            WHERE s.ts_acceptance IS NOT NULL
            SET s.ts_acceptance =
                    CASE
                        WHEN coalesce(s.ts_acceptance, 0) + $ts_delta > 100 THEN 100
                        WHEN coalesce(s.ts_acceptance, 0) + $ts_delta < 0 THEN 0
                        ELSE coalesce(s.ts_acceptance, 0) + $ts_delta
                    END,
                s.northern_attachment =
                    CASE
                        WHEN coalesce(s.northern_attachment, 0) + $na_delta > 100 THEN 100
                        WHEN coalesce(s.northern_attachment, 0) + $na_delta < 0 THEN 0
                        ELSE coalesce(s.northern_attachment, 0) + $na_delta
                    END
            """,
            npc_id=npc_id, ts_delta=ts_delta, na_delta=na_delta,
        )

    if ts_delta:
        print(f"[AcceptanceUpdater] {npc_id}: ts_acceptance +{ts_delta}")
    if na_delta:
        print(f"[AcceptanceUpdater] {npc_id}: northern_attachment +{na_delta}")


async def _unique_event_id(base_id: str) -> str:
    """기존 Event id와 충돌하지 않는 id를 반환한다."""
    safe_base = re.sub(r"[^0-9A-Za-z가-힣_\-]+", "_", str(base_id)).strip("_")
    if not safe_base:
        safe_base = f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async with async_driver.session() as session:
        for idx in range(100):
            candidate = safe_base if idx == 0 else f"{safe_base}_{idx + 1}"
            rec = await session.run(
                "MATCH (e:Event {id: $eid}) RETURN e.id AS id",
                eid=candidate,
            )
            if await rec.single() is None:
                return candidate

    return f"{safe_base}_{datetime.now().strftime('%H%M%S%f')}"


async def _create_event(event_data: dict, npc_id: str, pc_id: str) -> None:
    """Event 노드 생성 + Memory 노드 생성까지 처리한다."""
    if not event_data or not event_data.get("id"):
        return

    event_data["id"] = await _unique_event_id(str(event_data["id"]))
    timestamp_fmt = await get_in_universe_time()
    timestamp_iso = await _get_current_iso_time()
    prepared = _prepare_event_summaries(event_data)
    summary = prepared["summary"]
    importance = prepared["importance"] or 5

    embedding = None
    embedding_text = prepared["narrative_summary"] or summary
    if embedding_text:
        try:
            embedding = await embed_async(embedding_text)
        except Exception as e:
            print(f"[Updater] 임베딩 생성 실패 (무시): {e}")

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Event {
                id:            $id,
                summary:       $summary,
                timestamp:     $timestamp,
                importance:    $importance,
                impact:        $impact,
                memory_type:   $memory_type,
                narrative_summary: $narrative_summary,
                state_summary: $state_summary,
                decay_rate:    0.1,
                summary_level: 0,
                embedding:     $embedding
            })
        """,
            id=event_data["id"], summary=summary, timestamp=timestamp_fmt,
            importance=importance, impact=prepared["impact"],
            memory_type=prepared["memory_type"],
            narrative_summary=prepared["narrative_summary"],
            state_summary=prepared["state_summary"],
            embedding=embedding,
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

    print(f"[Updater] event created: {event_data['id']} (importance={importance})")

    await ensure_memories_for_event(
        event_id   = event_data["id"],
        summary    = summary,
        importance = importance,
        char_ids   = [npc_id, pc_id],
        timestamp  = timestamp_iso,
        embedding  = embedding,
        memory_type=prepared["memory_type"],
        narrative_summary=prepared["narrative_summary"],
        state_summary=prepared["state_summary"],
    )


async def update_relationship_narrative(
    npc_id: str,
    pc_id: str,
    event_summary: str,
    event_importance: int,
) -> None:
    """중요 이벤트(importance >= 6) 발생 후 RELATIONSHIP.summary를 Pro 모델로 재작성한다."""
    from src.core.llm.client import get_model, extract_json_from_llm

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b}) "
            "RETURN r.summary AS summary, r.affinity AS affinity, r.trust AS trust",
            a=npc_id, b=pc_id,
        )
        row = await rec.single()

    if not row:
        return

    current_summary = row.get("summary") or ""
    affinity = row.get("affinity") or 0
    trust = row.get("trust") or 0

    prompt = f"""Update this relationship summary based on a significant new event.

Previous summary: {current_summary or "(none yet)"}
Current state: affinity={affinity}, trust={trust}
New event (importance={event_importance}): {event_summary}

Write a new 2-3 sentence relationship summary reflecting the state AFTER this event.
Capture: how they relate now, emotional undercurrents, unresolved tensions or new intimacy.
Present tense. Korean is fine.

Return ONLY JSON:
{{"summary": "..."}}"""

    try:
        model = get_model(
            model_name=NARRATIVE_MODEL,
            system_prompt="You are a relationship analyst for a roleplay system. Write concise, specific relationship summaries.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.2, "max_output_tokens": 256, "response_mime_type": "application/json"},
        )
        result = extract_json_from_llm(resp.text, source="relationship_narrative")
        new_summary = result.get("summary") if isinstance(result, dict) else None
        if not new_summary:
            return

        _esc = new_summary.replace("\\", "\\\\").replace("'", "\\'")
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (a:Character {{id: $a}})-[r:RELATIONSHIP]->(b:Character {{id: $b}}) SET r.summary = '{_esc}'",
                a=npc_id, b=pc_id,
            )
        print(f"[RelationshipNarrative] {npc_id}↔{pc_id} 갱신 완료")
    except Exception as e:
        print(f"[RelationshipNarrative] 갱신 실패 (무시): {e}")


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
    event_only=True 전용 경로. OOC 트리거(부상·입원)에서만 호출.
    Event 생성 + relationship_status 갱신만 수행한다.
    DynamicState / 호감도 업데이트는 포함하지 않는다.
    """
    from src.simulation.systems.social import resolve_and_update as wb_resolve
    from src.simulation.state.relationships import apply_scene_relationship_updates

    plan = await _generate_event_plan(
        input_text      = actor_response,
        npc_id          = npc_id,
        pc_id           = pc_id,
        initial_changes = initial_changes or {},
    )

    new_event = plan.get("new_event")
    if new_event:
        await _create_event(new_event, npc_id, pc_id)
        new_status = new_event.get("new_relationship_status")
        if new_status and new_event.get("importance", 0) >= 7:
            await _apply_relationship_status(npc_id, pc_id, new_status)

    if world_config and scene_chars:
        try:
            participant_ids = await wb_resolve(
                char_names       = scene_chars,
                main_npc_id      = npc_id,
                pc_id            = pc_id,
                world_config     = world_config,
                event_id         = new_event.get("id") if new_event else None,
                event_importance = new_event.get("importance", 0) if new_event else 0,
            )
            await apply_scene_relationship_updates(
                actor_response,
                [pc_id, npc_id, *participant_ids],
                primary_pair=(npc_id, pc_id),
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")
