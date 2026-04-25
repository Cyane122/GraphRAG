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