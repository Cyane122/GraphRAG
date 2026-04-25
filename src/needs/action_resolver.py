"""
욕구 1회 초과 시 Haiku가 NPC가 뭘 했을지 결정.
→ Event 노드 생성 + 욕구 수치 감소 반영.

Libido / Safety는 이 파일에서 처리하지 않음.
  - Libido: needs_manager가 hint만 반환
  - Safety: complex_updater의 Event 연동으로 관리
"""

import os
from datetime import datetime, timedelta

from src.utils.db_utils import async_driver, update_dynamic_state, get_in_universe_time
from src.utils.llm_utils import llm_client, extract_json_from_llm
from src.utils.embedder import embed_async

ACTION_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

# 해소 후 안착 수치
SETTLE_LEVELS = {
    "hunger": 0.15,
    "rest":   0.10,
    "social": 0.20,
    "fun":    0.20,
}

# 해소 가능한 욕구 → 행동 힌트
NEED_ACTION_HINTS = {
    "hunger": "finding food / eating a meal",
    "rest":   "resting / sleeping / lying down",
    "social": "contacting someone / meeting a friend / texting",
    "fun":    "watching a video / gaming / doing something enjoyable",
}


async def resolve_action(
    npc_id:       str,
    need_name:    str,
    overflow_time: datetime,   # 0.8 초과 시점
    location_id:  str,
    personality:  str,
    traits:       dict,
    sexual_tendency: list[str],
) -> dict | None:
    """
    Haiku에게 npc_id가 overflow_time에 뭘 했는지 결정하게 함.
    Event 노드 생성 후 DynamicState 욕구 수치 감소.

    Returns: 생성된 event dict, 실패 시 None.
    """
    if need_name not in SETTLE_LEVELS:
        return None

    hint = NEED_ACTION_HINTS.get(need_name, "doing something to address their needs")

    # ── 1. Haiku 행동 결정 ──────────────────────────────────
    action = await _decide_action(
        npc_id, need_name, hint, overflow_time, location_id, personality, traits
    )
    if not action:
        return None

    # ── 2. Event 노드 생성 ──────────────────────────────────
    event_id = await _create_event(npc_id, action, overflow_time, location_id)

    # ── 3. 욕구 수치 감소 ────────────────────────────────────
    await _settle_need(npc_id, need_name)

    return {"event_id": event_id, **action}


# ════════════════════════════════════════════════════════════
# Internal
# ════════════════════════════════════════════════════════════

async def _decide_action(
    npc_id:       str,
    need_name:    str,
    hint:         str,
    overflow_time: datetime,
    location_id:  str,
    personality:  str,
    traits:       dict,
) -> dict | None:
    trait_summary = ", ".join(
        f"{k.replace('trait_', '')}={v:+.1f}"
        for k, v in traits.items()
        if abs(v) >= 0.4
    )
    time_str = overflow_time.strftime("%Y-%m-%d %H:%M")

    prompt = f"""You are an NPC behavior engine for a Korean slice-of-life roleplay.

NPC: {npc_id}
Personality: {personality}
Key traits: {trait_summary or "neutral"}
Location at time: {location_id}
Overflowing need: {need_name} (level reached 0.8)
Time of overflow: {time_str}
Likely behavior category: {hint}

Decide exactly what this NPC did to address their need.
Be specific but brief. Match the personality. Keep it mundane and realistic.

Return ONLY JSON:
{{
  "action_summary": "...",     // 1 sentence, Korean OK, what they did
  "target_location_id": "...", // where they went (use existing loc id or same location)
  "duration_minutes": 20,      // how long it took (int)
  "importance": 1              // always 1 for autonomous daily needs
}}"""

    try:
        resp = llm_client.messages.create(
            model=ACTION_MODEL,
            max_tokens=256,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        parsed = extract_json_from_llm(raw)
        if not isinstance(parsed, dict) or "action_summary" not in parsed:
            raise ValueError("invalid structure")
        return parsed
    except Exception as e:
        print(f"[ActionResolver] {npc_id}/{need_name} 행동 결정 실패: {e}")
        return None


async def _create_event(
    npc_id:       str,
    action:       dict,
    overflow_time: datetime,
    origin_loc_id: str,
) -> str:
    ts = overflow_time.strftime("%Y%m%d_%H%M")
    event_id  = f"{origin_loc_id}_{npc_id}_auto_{ts}"
    summary   = action.get("action_summary", "")
    target_loc = action.get("target_location_id", origin_loc_id)
    importance = int(action.get("importance", 1))

    # 임베딩 (실패 시 None — Vector Index 미등록)
    try:
        embedding = await embed_async(summary)
    except Exception:
        embedding = None

    async with async_driver.session() as session:
        await session.run("""
            CREATE (e:Event {
                id:               $eid,
                summary:          $summary,
                timestamp:        $ts,
                location_id:      $loc,
                impact:           "autonomous need resolution",
                importance:       $importance,
                decay_rate:       0.05,
                summary_level:    0,
                safety_impact:    0.0,
                safety_resolved:  true,
                safety_decay_rate: 0.0,
                embedding:        $emb
            })
        """, eid=event_id, summary=summary, ts=overflow_time.isoformat(),
             loc=target_loc, importance=importance, emb=embedding)

        # NPC ─ INVOLVED_IN ─ Event
        await session.run("""
            MATCH (c:Character {id: $cid}), (e:Event {id: $eid})
            CREATE (c)-[:INVOLVED_IN]->(e)
        """, cid=npc_id, eid=event_id)

        # Event ─ OCCURRED_AT ─ Location
        await session.run("""
            MATCH (e:Event {id: $eid}), (l:Location {id: $loc})
            CREATE (e)-[:OCCURRED_AT]->(l)
        """, eid=event_id, loc=target_loc)

    return event_id


async def _settle_need(npc_id: str, need_name: str) -> None:
    """욕구 수치를 해소 후 안착값으로 내림."""
    settle_val = SETTLE_LEVELS.get(need_name, 0.2)

    # DynamicState가 있는 경우 (main NPC)
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d.id AS did
        """, cid=npc_id)
        row = await rec.single()

    if row:
        await update_dynamic_state(npc_id, {need_name: settle_val})
        return

    # NeedsState가 있는 경우 (secondary NPC)
    async with async_driver.session() as session:
        await session.run(f"""
            MATCH (c:Character {{id: $cid}})-[:HAS_NEEDS]->(n:NeedsState)
            SET n.{need_name} = $val
        """, cid=npc_id, val=settle_val)