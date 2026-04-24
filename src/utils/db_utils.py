import os
from datetime import datetime

from neo4j import AsyncGraphDatabase
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")

async_driver = AsyncGraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)


async def update_dynamic_state(char_id: str, updates: dict) -> None:
    """DynamicState 노드 속성 공통 업데이트."""
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState) SET {set_clause}",
            char_id=char_id, **updates,
        )


async def update_relationship_affinity(char_a: str, char_b: str, delta: int) -> None:
    """호감도 공통 업데이트 (양방향, ±100 상한)."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.affinity = CASE
                WHEN r.affinity + $delta > 100  THEN 100
                WHEN r.affinity + $delta < -100 THEN -100
                ELSE r.affinity + $delta
            END
        """, a=char_a, b=char_b, delta=delta)


async def move_location(char_id: str, new_loc_id: str) -> None:
    """캐릭터 장소 이동 공통 로직."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(prev:Location)
            DELETE old
            SET prev.current_chars = [x IN prev.current_chars WHERE x <> $char_id]
        """, char_id=char_id)
        await session.run("""
            MATCH (c:Character {id: $char_id})
            MATCH (next:Location {id: $new_loc_id})
            MERGE (c)-[:LOCATED_AT]->(next)
            SET next.current_chars = coalesce(next.current_chars, []) + [$char_id]
        """, char_id=char_id, new_loc_id=new_loc_id)
        await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            SET d.location_id = $new_loc_id
        """, char_id=char_id, new_loc_id=new_loc_id)


async def advance_cycle_day(char_id: str, days: int) -> None:
    """생리/바이오리듬 일자 공통 업데이트."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            WHERE d.cycle_day IS NOT NULL
            SET d.cycle_day = ((d.cycle_day + $days - 1) % 28) + 1
        """, char_id=char_id, days=days)


async def get_in_universe_time() -> str:
    """GlobalState에서 현재 인게임 시간을 YYYYMMDD_HHMM 형식으로 반환."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await rec.single()
        if row and row["ct"]:
            return datetime.fromisoformat(row["ct"]).strftime("%Y%m%d_%H%M")
    return "20240101_0000"


async def fetch_similar_events(
    char_id: str,
    query_embedding: list[float],
    limit: int = 2,
    score_threshold: float = 0.70,
) -> list[dict]:
    """
    Vector Index를 이용해 query_embedding과 의미적으로 유사한 Event를 검색.

    - char_id와 INVOLVED_IN 관계가 있는 Event만 반환.
    - embedding 속성이 없는 레거시 이벤트는 자동으로 제외됨 (Index 미등록).
    - score_threshold 이하 결과 제외.

    반환: [{"id": str, "summary": str, "timestamp": str, "impact": str, "score": float}, ...]
    """
    async with async_driver.session() as session:
        # 상위 N*5개 후보 검색 후 character 필터 + 임계값 적용 → limit
        records = await session.run("""
            CALL db.index.vector.queryNodes('event_embeddings', $candidates, $embedding)
            YIELD node AS event, score
            MATCH (c:Character {id: $char_id})-[:INVOLVED_IN]->(event)
            WHERE score >= $threshold
            RETURN event.id        AS id,
                   event.summary   AS summary,
                   event.timestamp AS timestamp,
                   event.impact    AS impact,
                   score
            ORDER BY score DESC
            LIMIT $limit
        """,
            char_id=char_id,
            embedding=query_embedding,
            candidates=limit * 5,
            threshold=score_threshold,
            limit=limit,
        )
        rows = await records.data()
        return [dict(r) for r in rows]