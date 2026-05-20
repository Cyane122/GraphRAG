# ================================
# src/simulation/systems/memory/distortion.py
#
# DB helpers for memory distortion, deletion, and trait lookup.
#
# Functions
#   - _distort_memories_batch(memories: list[dict], char_id: str, traits: dict) -> dict[str, str] : Distort memories in a batch
#   - distort_on_affinity_change(char_id: str, pc_id: str, affinity_delta: int, current_game_time: datetime) -> None : Distort memories after affinity changes
#   - _update_memory(mem_id: str, updates: dict) -> None : Update Memory fields
#   - _delete_memory(mem_id: str) -> None : Delete a Memory
#   - _fetch_char_traits(char_id: str) -> dict : Fetch character traits
# ================================
import json
from datetime import datetime

from src.config import MODEL_STATE_UPDATER as DECAY_MODEL
from src.core.database import async_driver
from src.core.embedding.encoder import embed_async
from src.core.llm.client import get_model, extract_json_from_llm

def _build_trait_hints(traits: dict) -> list[str]:
    """trait 딕셔너리에서 기억 왜곡 방향 힌트를 생성한다."""
    hints: list[str] = []
    if traits.get("trait_self_esteem", 0) > 0.5:
        hints.append("tends to remember things more positively, downplays negatives")
    if traits.get("trait_anxiety_prone", 0) > 0.5:
        hints.append("tends to amplify threatening or upsetting elements")
    if traits.get("trait_self_esteem", 0) < -0.1:
        hints.append("tends to underplay their own role or worth")
    if traits.get("trait_stubbornness", 0) > 0.5:
        hints.append("tends to remember their own position as more justified")
    if traits.get("trait_attachment", 0) > 0.7:
        hints.append("keeps partner-related details vivid, fades other details")
    if traits.get("trait_jealousy", 0) > 0.5:
        hints.append("tends to over-remember moments of perceived rivalry or slight")
    return hints


async def _distort_memories_with_hints(
    memories: list[dict],
    char_id:  str,
    hints:    list[str],
) -> dict[str, str]:
    """
    주어진 힌트로 기억 목록을 왜곡한다.
    Returns: {mid: new_summary} — 힌트가 없으면 빈 dict.
    """
    if not hints or not memories:
        return {}

    hint_str = "; ".join(hints)
    items    = "\n".join(
        f'{i + 1}. [id:{m["mid"]}] {m["summary"]}'
        for i, m in enumerate(memories)
    )

    prompt = f"""Rewrite each memory from {char_id}'s perspective. Subtle distortion: {hint_str}
Keep core facts; shift emphasis/tone/minor details only. 1-2 sentences. Korean OK.

{items}

Return ONLY JSON array: [{{"id":"<mid>","summary":"<rewritten>"}},...]"""

    try:
        model = get_model(
            DECAY_MODEL,
            system_prompt=f"{char_id}'s inner subconscious — rewrite memories from their subjective perspective.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 128 * len(memories) + 128,
                "temperature": 0.7,
                "response_mime_type": "application/json",
            },
        )
        results = extract_json_from_llm(resp.text, source="memory_distort_batch")
        if isinstance(results, list):
            return {
                item["id"]: item["summary"]
                for item in results
                if isinstance(item, dict) and "id" in item and "summary" in item
            }
    except Exception as e:
        print(f"[DecayManager] 배치 왜곡 실패 ({char_id}): {e}")
    return {}


async def _distort_memories_batch(
    memories: list[dict],
    char_id:  str,
    traits:   dict,
) -> dict[str, str]:
    """
    동일 캐릭터의 여러 Memory를 trait 기반으로 한 번에 왜곡한다.
    trait_hints가 없으면 왜곡 없이 빈 dict 반환.
    Returns: {mid: new_summary}
    """
    hints = _build_trait_hints(traits)
    return await _distort_memories_with_hints(memories, char_id, hints)


# ════════════════════════════════════════════════════════════
# 3. 호감도 급변 즉시 왜곡
# ════════════════════════════════════════════════════════════

async def distort_on_affinity_change(
    char_id:           str,
    pc_id:             str,
    affinity_delta:    int,
    current_game_time: datetime,
) -> None:
    """
    호감도 급변(|delta| >= 10) 시 공유 기억을 즉시 재해석한다.
    긍정 delta → 부정 기억 완화, 부정 delta → 중립·긍정 기억 어둡게 변형.
    시간 기반 decay와 별개로 트리거된다.
    """
    if abs(affinity_delta) < 10:
        return

    memories = await _fetch_shared_memories(char_id, pc_id)
    if not memories:
        return

    traits = await _fetch_char_traits(char_id)

    # 방향에 따른 즉각적인 재해석 힌트 — trait 기반 힌트와 결합
    direction_hints: list[str] = []
    if affinity_delta > 0:
        direction_hints.append("relationship just deepened — recall warmer aspects, soften previous tension")
        direction_hints.append("reinterpret ambiguous moments more favorably")
    else:
        direction_hints.append("relationship just soured — recall warning signs previously overlooked")
        direction_hints.append("reinterpret neutral moments with a trace of unease or suspicion")

    all_hints = list(set(direction_hints + _build_trait_hints(traits)))
    results   = await _distort_memories_with_hints(memories, char_id, all_hints)

    for m in memories:
        new_summary = results.get(m["mid"])
        if new_summary and new_summary != m["summary"]:
            distortion = float(m["distortion"] or 0)
            await _update_memory(
                m["mid"], new_summary, None,
                int(m["level"]), min(1.0, distortion + 0.2),
                current_game_time,
            )

    if results:
        print(f"[DecayManager] affinity 왜곡: {char_id} ({len(results)}개, delta={affinity_delta:+d})")


async def _fetch_shared_memories(char_id: str, pc_id: str) -> list[dict]:
    """
    char_id의 기억 중 pc_id가 함께 관련된 이벤트 기억 최대 3개 반환.
    이미 많이 왜곡됐거나 압축된 기억, 중요도 높은 기억은 제외한다.
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $char_id})-[:REMEMBERS]->(m:Memory)
            WHERE m.importance <= 6
              AND m.distortion_level < 0.75
              AND m.summary_level < 2
            MATCH (m)-[:OF_EVENT]->(e:Event)
            MATCH (pc:Character {id: $pc_id})-[:INVOLVED_IN]->(e)
            RETURN m.id               AS mid,
                   CASE
                       WHEN m.narrative_summary IS NULL OR m.narrative_summary = '' THEN m.summary
                       ELSE m.narrative_summary
                   END                AS summary,
                   m.importance       AS importance,
                   m.distortion_level AS distortion,
                   m.summary_level    AS level
            ORDER BY e.timestamp DESC
            LIMIT 3
        """, char_id=char_id, pc_id=pc_id)
        rows = await rec.data()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# 내부 헬퍼
# ════════════════════════════════════════════════════════════

async def _update_memory(
    mem_id:        str,
    new_summary:   str,
    old_embedding: list[float] | None,
    summary_level: int,
    distortion:    float,
    game_time:     datetime,
) -> None:
    """Memory 노드 요약/임베딩/레벨/왜곡도를 갱신한다."""
    new_emb = None
    try:
        new_emb = await embed_async(new_summary)
    except Exception as e:
        print(f"[DecayManager] 재임베딩 실패, 기존 임베딩 유지: {e}")

    async with async_driver.session() as session:
        if new_emb is not None:
            await session.run("""
                MATCH (m:Memory {id: $mid})
                SET m.summary          = $summary,
                    m.narrative_summary = $summary,
                    m.embedding        = $emb,
                    m.summary_level    = $level,
                    m.distortion_level = $distortion,
                    m.last_decayed_at  = $ts
            """, mid=mem_id, summary=new_summary, emb=new_emb,
                 level=summary_level, distortion=distortion, ts=game_time.isoformat())
        else:
            await session.run("""
                MATCH (m:Memory {id: $mid})
                SET m.summary          = $summary,
                    m.narrative_summary = $summary,
                    m.summary_level    = $level,
                    m.distortion_level = $distortion,
                    m.last_decayed_at  = $ts
            """, mid=mem_id, summary=new_summary,
                 level=summary_level, distortion=distortion, ts=game_time.isoformat())


async def _delete_memory(mem_id: str) -> None:
    """Memory 노드를 그래프에서 완전 삭제한다."""
    async with async_driver.session() as session:
        await session.run(
            "MATCH (m:Memory {id: $mid}) DETACH DELETE m",
            mid=mem_id,
        )
    print(f"[DecayManager] Memory 삭제: {mem_id}")


async def _fetch_char_traits(char_id: str) -> dict:
    """DynamicState → StaticProfile 순으로 trait_* 필드 로드.
    StaticProfile은 JSON blob이므로 props 컬럼을 파싱해 trait_* 키를 추출한다.
    """
    async with async_driver.session() as session:
        for rel in ("HAS_STATE", "HAS_PROFILE"):
            rec = await session.run(f"""
                MATCH (c:Character {{id: $cid}})-[:{rel}]->(n)
                RETURN n AS props
            """, cid=char_id)
            row = await rec.single()
            if not row or not row["props"]:
                continue
            props = dict(row["props"])
            # JSON blob 노드: {"id":…, "props":"…json…"} → 내부 JSON 파싱
            if isinstance(props.get("props"), str):
                try:
                    props = json.loads(props["props"])
                except (ValueError, TypeError):
                    pass
            traits = {k: v for k, v in props.items() if k.startswith("trait_")}
            if traits:
                return traits
    return {}
