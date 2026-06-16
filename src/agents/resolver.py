# ================================
# src/agents/resolver.py
#
# 욕구(Needs) 초과 시 NPC 자율 행동을 결정하고 Event + 욕구 감소를 반영합니다.
#
# Functions
#   - resolve_action(npc_id: str, need_name: str, overflow_time: datetime, location_id: str, personality: str, traits: dict, extra_context: str = "", valid_locations: list[dict] | None = None) -> dict | None : 욕구 초과 NPC 행동 결정 및 이벤트 생성
# ================================

from datetime import datetime

from src.config import MODEL_STATE_UPDATER as ACTION_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
# 욕구 기준 상수의 단일 출처는 needs 패키지다(레이어 역방향 import 제거).
from src.simulation.systems.needs.models import NEED_DEFAULTS, SETTLE_LEVELS

# 해소 가능한 욕구 → 행동 힌트
NEED_ACTION_HINTS = {
    "hunger": "finding food / eating a meal",
    "rest":   "resting / sleeping / lying down",
    "social": "contacting someone / meeting a friend / texting",
    "fun":    "watching a video / gaming / doing something enjoyable",
    "libido": "resolving sexual tension — with a partner if available, otherwise privately (masturbation, cold shower, distraction)",
}


async def _unique_event_id(base_id: str) -> str:
    """Return an Event id that does not collide with an existing node."""
    async with async_driver.session() as session:
        for idx in range(100):
            candidate = base_id if idx == 0 else f"{base_id}_{idx + 1}"
            rec = await session.run(
                "MATCH (e:Event {id: $eid}) RETURN e.id AS id",
                eid=candidate,
            )
            if await rec.single() is None:
                return candidate
    return f"{base_id}_{datetime.now().strftime('%H%M%S%f')}"


async def _fetch_valid_locations() -> list[dict]:
    """DB에서 유효한 Location ID, 이름, 태그, 상위 장소 목록을 반환합니다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (l:Location)
            OPTIONAL MATCH (l)-[:PART_OF]->(p:Location)
            RETURN l.id AS id, l.name AS name, l.tags AS tags, collect(p.id) AS parent_ids
        """)
        rows = await rec.fetch_all()
    return [
        {
            "id": r["id"],
            "name": r.get("name") or r["id"],
            "tags": r.get("tags") or [],
            "parent_ids": r.get("parent_ids") or [],
        }
        for r in rows
        if r["id"]
    ]


async def resolve_action(
    npc_id:        str,
    need_name:     str,
    overflow_time: datetime,
    location_id:   str,
    personality:   str,
    traits:        dict,
    extra_context: str = "",
    valid_locations: list[dict] | None = None,
) -> dict | None:
    """
    Haiku에게 npc_id가 overflow_time에 뭘 했는지 결정하게 함.
    Event 노드 생성 후 DynamicState 욕구 수치 감소.

    extra_context: libido처럼 추가 정보가 필요한 욕구에서 프롬프트에 삽입할 문자열.
    valid_locations: 욕구별로 사전 필터링된 장소 후보. None이면 전체 Location을 사용.
    Returns: 생성된 event dict, 실패 시 None.
    """
    if need_name not in SETTLE_LEVELS:
        return None

    valid_locations = valid_locations if valid_locations is not None else await _fetch_valid_locations()
    if not valid_locations:
        valid_locations = [{
            "id": location_id,
            "name": location_id,
            "tags": [],
            "parent_ids": [],
        }]
    hint   = NEED_ACTION_HINTS.get(need_name, "doing something to address their needs")
    action = await _decide_action(
        npc_id, need_name, hint, overflow_time, location_id, personality, traits,
        valid_locations=valid_locations,
        extra_context=extra_context,
    )
    if not action:
        return None

    valid_loc_ids = {str(loc.get("id")) for loc in valid_locations if loc.get("id")}
    event_id = await _create_event(
        npc_id, action, overflow_time, location_id,
        need_name=need_name,
        valid_loc_ids=valid_loc_ids,
    )
    await _settle_need(npc_id, need_name)

    return {"event_id": event_id, **action}


# ════════════════════════════════════════════════════════════
# Internal
# ════════════════════════════════════════════════════════════

async def _decide_action(
    npc_id:          str,
    need_name:       str,
    hint:            str,
    overflow_time:   datetime,
    location_id:     str,
    personality:     str,
    traits:          dict,
    valid_locations: list[dict] | None = None,
    extra_context:   str = "",
) -> dict | None:
    """LLM에게 욕구 해소 행동을 결정하게 하고 구조화된 dict를 반환한다."""
    trait_summary = ", ".join(
        f"{k.replace('trait_', '')}={v:+.1f}"
        for k, v in traits.items()
        if abs(v) >= 0.4
    )
    time_str = overflow_time.strftime("%Y-%m-%d %H:%M")

    loc_list = "\n".join(
        f'  - "{loc.get("id")}" ({loc.get("name") or loc.get("id")})'
        for loc in (valid_locations or [])
        if loc.get("id")
    )
    if not loc_list:
        loc_list = f'  - "{location_id}" (current location)'

    extra_block = f"\nAdditional context: {extra_context}" if extra_context else ""
    system_instruction = "You are an NPC behavior engine for a Korean slice-of-life roleplay."

    prompt = f"""NPC: {npc_id}
Personality: {personality}
Key traits: {trait_summary or "neutral"}
Location at time: {location_id}
Overflowing need: {need_name} (level reached 0.8)
Time of overflow: {time_str}
Likely behavior category: {hint}{extra_block}

Valid location IDs (use EXACTLY one of these):
{loc_list}

Decide exactly what this NPC did to address their need.
Be specific but brief. Match the personality. Keep it mundane and realistic.

Return ONLY valid JSON. Never use "..." as a value — always write the complete string:
{{
  "action_summary": "편의점에서 컵라면을 먹었다",  // 1 sentence, Korean, complete
  "target_location_id": "exact_id_from_list_above", // MUST be one of the valid IDs above
  "duration_minutes": 20,      // how long it took (int)
  "importance": 1              // always 1 for autonomous daily needs
}}"""

    try:
        model = get_model(ACTION_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 1024,
                "temperature": 0.7,
                "response_mime_type": "application/json",
            }
        )
        parsed = extract_json_from_llm(resp.text)
        if not isinstance(parsed, dict) or "action_summary" not in parsed:
            raise ValueError("invalid structure")
        return parsed
    except Exception as e:
        print(f"[ActionResolver] {npc_id}/{need_name} 행동 결정 실패: {e}")
        return None


async def _create_event(
    npc_id:         str,
    action:         dict,
    overflow_time:  datetime,
    origin_loc_id:  str,
    need_name:      str = "",
    valid_loc_ids:  set[str] | None = None,
) -> str:
    ts         = overflow_time.strftime("%Y%m%d_%H%M")
    need_part  = need_name or "need"
    event_id   = await _unique_event_id(f"{origin_loc_id}_{npc_id}_{need_part}_auto_{ts}")
    summary    = action.get("action_summary", "")
    target_loc = action.get("target_location_id", "") or origin_loc_id
    if valid_loc_ids and target_loc not in valid_loc_ids:
        target_loc = origin_loc_id
    importance = int(action.get("importance") or 1)

    async with async_driver.session() as session:
        await session.run("""
            CREATE (e:Event {
                id:                $eid,
                summary:           $summary,
                timestamp:         $ts,
                location_id:       $loc,
                impact:            "autonomous need resolution",
                need_name:         $need_name,
                importance:        $importance,
                decay_rate:        0.05,
                summary_level:     0,
                safety_impact:     0.0,
                safety_resolved:   true,
                safety_decay_rate: 0.0
            })
        """, eid=event_id, summary=summary, ts=overflow_time.isoformat(),
             loc=target_loc, importance=importance, need_name=need_name)

        await session.run("""
            MATCH (c:Character {id: $cid}), (e:Event {id: $eid})
            CREATE (c)-[:INVOLVED_IN]->(e)
        """, cid=npc_id, eid=event_id)

        await session.run("""
            MATCH (e:Event {id: $eid}), (l:Location {id: $loc})
            CREATE (e)-[:OCCURRED_AT]->(l)
        """, eid=event_id, loc=target_loc)

    return event_id


async def _settle_need(npc_id: str, need_name: str) -> None:
    """Settle a need value on the character's NeedsState node."""
    settle_val = SETTLE_LEVELS.get(need_name, 0.2)

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN n.id AS nid
        """, cid=npc_id)
        row = await rec.single()

        if not row:
            defaults = dict(NEED_DEFAULTS)
            defaults[need_name] = settle_val
            defaults["id"] = f"{npc_id}_needs"
            await session.run("""
                MATCH (c:Character {id: $cid})
                CREATE (c)-[:HAS_NEEDS]->(n:NeedsState {
                    id:     $id,
                    hunger: $hunger,
                    rest:   $rest,
                    social: $social,
                    fun:    $fun,
                    safety: $safety,
                    libido: $libido
                })
            """, cid=npc_id, **defaults)
            return

        await session.run(f"""
            MATCH (c:Character {{id: $cid}})-[:HAS_NEEDS]->(n:NeedsState)
            SET n.{need_name} = $val
        """, cid=npc_id, val=settle_val)
