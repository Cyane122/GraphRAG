# ================================
# src/simulation/state/events.py
#
# Create Event records and handle event-only complex updates.
#
# Functions
#   - _create_event(event_data: dict, npc_id: str, pc_id: str, actor_response: str = "", participant_ids: list[str] | None = None) -> None : Create Event and Memory nodes
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


async def _fetch_active_event(npc_id: str, pc_id: str) -> dict | None:
    """RELATIONSHIP.active_event_id에서 현재 열린 이벤트를 조회한다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            WHERE r.active_event_id IS NOT NULL AND r.active_event_id <> ''
            RETURN r.active_event_id AS eid
        """, a=npc_id, b=pc_id)
        row = await rec.single()
    if not row or not row.get("eid"):
        return None
    eid = row["eid"]
    async with async_driver.session() as session:
        rec2 = await session.run("""
            MATCH (e:Event {id: $eid})
            RETURN e.id AS id, e.summary AS summary, e.content AS content,
                   e.turn_count AS turn_count, e.timestamp AS timestamp, e.importance AS importance
        """, eid=eid)
        row2 = await rec2.single()
    return dict(row2) if row2 else None


async def _append_to_event(event_id: str, actor_response: str) -> None:
    """이벤트에 새 턴 내용을 추가한다."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (e:Event {id: $eid})
            SET e.content = e.content + '\n---\n' + $addition,
                e.turn_count = coalesce(e.turn_count, 1) + 1
        """, eid=event_id, addition=actor_response[:3000])
    print(f"[EventAccum] turn appended: {event_id}")


async def _compress_event_content(content: str, turns: int) -> dict:
    """누적된 이벤트 내용을 LLM으로 압축해 최종 summary를 반환한다."""
    from src.core.llm.client import get_model, extract_json_from_llm

    prompt = f"""Compress {turns} turns of a roleplay event into a final record.

Content:
{content[:5000]}

Return ONLY valid JSON with factual Event text only. Do not add speculation, emotion attribution, or memory distortion:
{{
  "summary": "2-4 sentence Korean factual record (who, what, how it concluded)"
}}"""

    try:
        model = get_model(
            model_name=NARRATIVE_MODEL,
            system_prompt="Compress roleplay event content into an objective factual event record.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.0, "max_output_tokens": 512, "response_mime_type": "application/json"},
        )
        result = extract_json_from_llm(resp.text, source="event_compress")
        if isinstance(result, dict):
            return result
    except Exception as e:
        print(f"[EventAccum] 압축 실패 (무시): {e}")
    return {}


async def _update_event_memories(event_id: str, new_summaries: dict) -> None:
    """이벤트 닫힘 후 관련 Memory의 객관 참조 필드만 갱신한다."""
    summary = new_summaries.get("summary", "")
    if not summary:
        return
    async with async_driver.session() as session:
        await session.run("""
            MATCH (m:Memory)-[:OF_EVENT]->(e:Event {id: $eid})
            SET m.state_summary = $summary
        """, eid=event_id, summary=summary)


async def _close_event(event_id: str, npc_id: str, pc_id: str, actor_response: str = "") -> None:
    """이벤트를 닫고 닫힘 턴까지 포함해 최종 summary를 갱신한다."""
    if actor_response:
        await _append_to_event(event_id, actor_response)

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (e:Event {id: $eid}) RETURN e.content AS content, e.turn_count AS turns",
            eid=event_id,
        )
        row = await rec.single()
    if not row:
        return

    content = row.get("content") or ""
    turns = int(row.get("turns") or 1)
    new_summaries: dict = {}
    if content:
        new_summaries = await _compress_event_content(content, turns)

    async with async_driver.session() as session:
        await session.run(
            "MATCH (e:Event {id: $eid}) SET e.status = 'closed'",
            eid=event_id,
        )
        if new_summaries.get("summary"):
            await session.run("""
                MATCH (e:Event {id: $eid})
                SET e.summary = $s, e.narrative_summary = $ns, e.state_summary = $ss
            """, eid=event_id,
                 s=new_summaries["summary"],
                 ns=new_summaries.get("narrative_summary", ""),
                 ss=new_summaries.get("state_summary", ""))
        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.active_event_id = ''
        """, a=npc_id, b=pc_id)

    if new_summaries.get("summary"):
        await _update_event_memories(event_id, new_summaries)

    print(f"[EventAccum] closed: {event_id} ({turns} turns)")


async def _apply_relationship_status(char_a: str, char_b: str, new_status: str) -> None:
    """RELATIONSHIP 양방향 current_status를 갱신한다."""
    if not new_status:
        return

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

    system_instruction = f"Precise event recorder for a roleplay system. Focus on {npc_id}/{pc_id}."

    prompt = f"""## Initial Changes
{json.dumps(initial_changes, ensure_ascii=False)}

## Event Gate

Create event iff durable story state changes.
Durable = relationship context / location / commitment / injury / secret / gift / named encounter / multi-turn scene anchor.
Routine meal / sitting / waiting / casual talk / atmosphere -> null unless durable.

## Importance

8-10 = major: hospitalization / surgery / accident / confession.
5-7 = significant: major injury / near-breakup / very first emotional intimacy / public humiliation.
2-4 = minor durable: new injury / named character / promise / secret / gift / location transition / small durable conflict / repeated sex arrangement.
0-1 = routine / atmospheric.

## Fields

id = {{location}}_{{description}}_{{YYYYMMDD_HHMM}}.
summary = 1-2 sentence Korean factual record; observed facts only.
importance = 0..10.
memory_type = episodic | emotional | relational.
importance >= 7 -> new_relationship_status = 1-3 English sentences about post-event relationship attitude.
new_relationship_status != current action / position / scene activity / currently-now detail.

Return ONLY valid JSON:
{{
  "new_event": null
}}

Scene:
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


async def _create_event(
    event_data: dict,
    npc_id: str,
    pc_id: str,
    actor_response: str = "",
    participant_ids: list[str] | None = None,
) -> None:
    """Event 노드와 관련 캐릭터별 Memory 노드를 생성한다."""
    if not event_data or not event_data.get("id"):
        return

    event_data["id"] = await _unique_event_id(str(event_data["id"]))
    timestamp_fmt = await get_in_universe_time()
    timestamp_iso = await _get_current_iso_time()
    prepared = _prepare_event_summaries(event_data)
    summary = prepared["summary"]
    importance = prepared["importance"]
    related_char_ids = list(dict.fromkeys(participant_ids or [npc_id, pc_id]))

    embedding = None
    if summary:
        try:
            embedding = await embed_async(summary)
        except Exception as e:
            print(f"[Updater] 임베딩 생성 실패 (무시): {e}")

    content = actor_response[:3000] if actor_response else ""

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
                content:       $content,
                status:        'active',
                turn_count:    1,
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
            content=content,
            embedding=embedding,
        )

        for char_id in related_char_ids:
            await session.run("""
                MATCH (c:Character {id: $char_id})
                MATCH (e:Event {id: $event_id})
                CREATE (c)-[:INVOLVED_IN]->(e)
            """, char_id=char_id, event_id=event_data["id"])

        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.shared_events    = coalesce(r.shared_events, []) + [$event_id],
                r.last_interaction = $timestamp,
                r.active_event_id  = $event_id
        """, a=npc_id, b=pc_id, event_id=event_data["id"], timestamp=timestamp_fmt)

    print(f"[Updater] event created: {event_data['id']} (importance={importance})")

    await ensure_memories_for_event(
        event_id   = event_data["id"],
        summary    = summary,
        importance = importance,
        char_ids   = related_char_ids,
        timestamp  = timestamp_iso,
        embedding  = embedding,
        memory_type=prepared["memory_type"],
        actor_response=actor_response,
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

    prompt = f"""Update relationship summary after a significant event.

Previous: {current_summary or "(none yet)"}
State: affinity={affinity}, trust={trust}
Event (importance={event_importance}): {event_summary}

2-3 sentences reflecting relationship state AFTER. Capture how they regard each other, emotional undercurrents, unresolved tensions or new intimacy. Present tense. Korean OK. Do not describe what they are physically doing now.

Return ONLY JSON: {{"summary": "..."}}"""

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

    participant_ids = [pc_id, npc_id]
    if world_config and scene_chars:
        try:
            resolved_ids = await wb_resolve(
                char_names       = scene_chars,
                main_npc_id      = npc_id,
                pc_id            = pc_id,
                world_config     = world_config,
            )
            participant_ids = [pc_id, npc_id, *resolved_ids]
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")

    plan = await _generate_event_plan(
        input_text      = actor_response,
        npc_id          = npc_id,
        pc_id           = pc_id,
        initial_changes = initial_changes or {},
    )

    new_event = plan.get("new_event")
    if new_event:
        await _create_event(
            new_event,
            npc_id,
            pc_id,
            actor_response,
            participant_ids=participant_ids,
        )
        new_status = new_event.get("new_relationship_status")
        if new_status and new_event.get("importance", 0) >= 7:
            await _apply_relationship_status(npc_id, pc_id, new_status)

    if world_config and scene_chars:
        try:
            await apply_scene_relationship_updates(
                actor_response,
                participant_ids,
                primary_pair=(npc_id, pc_id),
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")
