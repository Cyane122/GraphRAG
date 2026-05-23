# ================================
# src/simulation/systems/reputation.py
#
# NPC 간 소문 전파 및 사회적 평판 변화를 담당합니다.
# 중요 이벤트(importance >= 5) 발생 후 source NPC의 지인에게
# 소문을 전달하고, 수신 NPC의 호감도와 기억을 갱신합니다.
#
# Functions
#   - propagate_gossip(event_summary: str, event_importance: int, relationship_delta: int, source_npc_id: str, pc_id: str, timestamp_iso: str, source_event_id: str | None = None) -> None : 이벤트 기반 소문 전파 및 수신 NPC 호감도·기억 갱신
# ================================

import json

from src.config import MODEL_STATE_UPDATER as GOSSIP_MODEL
from src.core.database import async_driver
from src.core.database import update_relationship_affinity
from src.core.llm.client import get_model, extract_json_from_llm
from src.core.embedding.encoder import embed_async

_GOSSIP_MIN_IMPORTANCE      = 5
_GOSSIP_MIN_ABS_DELTA       = 3
_GOSSIP_SOURCE_MIN_AFFINITY = 15
_GOSSIP_MAX_TARGETS         = 3
_GOSSIP_DELTA_RATIO         = 0.35


async def propagate_gossip(
    event_summary:      str,
    event_importance:   int,
    relationship_delta: int,
    source_npc_id:      str,
    pc_id:              str,
    timestamp_iso:      str,
    source_event_id:    str | None = None,
) -> None:
    """
    source_npc와 pc_id 간 중요 이벤트 발생 후 주변 NPC에게 소문을 전파한다.
    source_npc의 지인 중 pc_id와도 관계가 있는 Named NPC에게 gossip 기억 생성 및 호감도 조정.
    """
    if event_importance < _GOSSIP_MIN_IMPORTANCE:
        return
    if abs(relationship_delta) < _GOSSIP_MIN_ABS_DELTA:
        return

    targets = await _find_gossip_targets(source_npc_id, pc_id)
    if not targets:
        return

    gossip_results = await _generate_gossip_batch(
        event_summary, relationship_delta, source_npc_id, pc_id, targets
    )

    for item in gossip_results:
        target_id   = item.get("target_id")
        gossip_text = item.get("gossip_summary", "").strip()
        delta       = item.get("affinity_delta", 0)

        if not target_id or not gossip_text:
            continue

        if delta and isinstance(delta, (int, float)):
            await update_relationship_affinity(target_id, pc_id, int(delta))
            await update_relationship_affinity(pc_id, target_id, int(delta))

        gossip_event_id   = f"gossip_{source_npc_id}_{target_id}_{timestamp_iso[:10].replace('-', '')}"
        gossip_importance = max(3, event_importance - 2)

        embedding = None
        try:
            embedding = await embed_async(gossip_text)
        except Exception:
            pass

        await _create_gossip_memory(
            char_id         = target_id,
            event_id        = gossip_event_id,
            summary         = gossip_text,
            importance      = gossip_importance,
            timestamp       = timestamp_iso,
            embedding       = embedding,
            source_event_id = source_event_id,
        )

    print(f"[Reputation] 소문 전파: {source_npc_id} → {len(gossip_results)}명")


async def _find_gossip_targets(source_npc_id: str, pc_id: str) -> list[dict]:
    """
    source와 친밀하고(affinity >= 15), pc_id와도 관계가 있는 Named NPC 목록 반환.
    소문이 전파되려면 전달자(source)와 수신자(target) 모두 pc_id를 알아야 한다.
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (src:Character {id: $src})-[r1:RELATIONSHIP]->(target:Character)
            WHERE r1.affinity >= $min_aff
              AND target.id <> $pc_id
              AND target.type = 'named'
            MATCH (target)-[r2:RELATIONSHIP]->(pc:Character {id: $pc_id})
            RETURN target.id         AS target_id,
                   target.name       AS target_name,
                   r1.affinity       AS src_to_target,
                   r2.affinity       AS target_to_pc
            LIMIT $lim
        """,
            src     = source_npc_id,
            pc_id   = pc_id,
            min_aff = _GOSSIP_SOURCE_MIN_AFFINITY,
            lim     = _GOSSIP_MAX_TARGETS,
        )
        rows = await rec.data()
    return [dict(r) for r in rows]


async def _generate_gossip_batch(
    event_summary:      str,
    relationship_delta: int,
    source_npc_id:      str,
    pc_id:              str,
    targets:            list[dict],
) -> list[dict]:
    """
    수신 NPC들에게 전파할 소문 내용과 호감도 변화를 배치 생성한다.
    소문은 source의 시각이 약간 반영되어 원사건보다 드라마틱할 수 있다.
    Returns: [{"target_id": ..., "gossip_summary": ..., "affinity_delta": ...}]
    """
    direction = "negative" if relationship_delta < 0 else "positive"
    max_delta = max(1, int(abs(relationship_delta) * _GOSSIP_DELTA_RATIO))

    items = json.dumps([
        {
            "target_id":                t["target_id"],
            "target_name":              t["target_name"],
            "closeness_to_source":      t["src_to_target"],
            "current_affinity_with_pc": t["target_to_pc"],
        }
        for t in targets
    ], ensure_ascii=False)

    delta_hint = (
        f"negative (integer from {-max_delta} to -1)"
        if direction == "negative"
        else f"positive (integer from 1 to {max_delta})"
    )

    prompt = f"""{direction.capitalize()} event: {source_npc_id} ↔ {pc_id} — {event_summary}

{source_npc_id} tells each recipient below. For each:
gossip_summary: 1-2 Korean sentences, slightly colored by {source_npc_id}'s view (not perfectly accurate, slightly dramatized)
affinity_delta: {delta_hint}. Closer to source = stronger effect. Prior dislike amplifies negative reaction.

Recipients: {items}

Return ONLY JSON array: [{{"target_id":"...","gossip_summary":"...","affinity_delta":0}},...]"""

    try:
        model = get_model(
            GOSSIP_MODEL,
            system_prompt="Simulate gossip ripple effects in a social network. Realistic, nuanced reactions.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 256 * len(targets) + 128,
                "temperature":       0.65,
                "response_mime_type": "application/json",
            },
        )
        parsed = extract_json_from_llm(resp.text, source="gossip_batch")
        if isinstance(parsed, list):
            return parsed
    except Exception as e:
        print(f"[Reputation] gossip 배치 생성 실패: {e}")
    return []


async def _create_gossip_memory(
    char_id:         str,
    event_id:        str,
    summary:         str,
    importance:      int,
    timestamp:       str,
    embedding:       list[float] | None,
    source_event_id: str | None,
) -> None:
    """
    소문(gossip) Memory 노드를 생성하고 REMEMBERS 관계로 연결한다.
    전언(傳言) 내용은 주관적 Memory로 보존하되, 원 Event가 있으면 OF_EVENT로 연결한다.
    """
    mem_id = f"mem_{char_id}_{event_id}"

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (m:Memory {id: $mid}) RETURN m.id AS id", mid=mem_id
        )
        if await rec.single():
            return

        await session.run("""
            CREATE (m:Memory {
                id:               $mid,
                event_id:         $event_id,
                char_id:          $char_id,
                summary:          $summary,
                embedding:        $emb,
                importance:       $importance,
                distortion_level: 0.15,
                summary_level:    0,
                created_at:       $ts,
                last_decayed_at:  $ts
            })
        """,
            mid        = mem_id,
            event_id   = event_id,
            char_id    = char_id,
            summary    = summary,
            emb        = embedding,
            importance = importance,
            ts         = timestamp,
        )

        await session.run("""
            MATCH (c:Character {id: $cid}), (m:Memory {id: $mid})
            CREATE (c)-[:REMEMBERS]->(m)
        """, cid=char_id, mid=mem_id)

        if source_event_id:
            await session.run("""
                MATCH (m:Memory {id: $mid}), (e:Event {id: $eid})
                CREATE (m)-[:OF_EVENT]->(e)
            """, mid=mem_id, eid=source_event_id)
