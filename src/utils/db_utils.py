# src/utils/db_utils.py
import os
from neo4j import AsyncGraphDatabase
from dotenv import load_dotenv
from pathlib import Path

# 환경변수 로드
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# 싱글톤 비동기 드라이버 (모든 파일에서 공유)
async_driver = AsyncGraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

async def update_dynamic_state(char_id: str, updates: dict) -> None:
    """DynamicState 노드 속성 공통 업데이트"""
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    async with async_driver.session() as session:
        await session.run(f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            SET {set_clause}
        """, char_id=char_id, **updates)

async def update_relationship_affinity(char_a: str, char_b: str, delta: int) -> None:
    """호감도(Affinity) 공통 업데이트 로직 (양방향/상한하한 적용)"""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.affinity = CASE
                WHEN r.affinity + $delta > 100 THEN 100
                WHEN r.affinity + $delta < -100 THEN -100
                ELSE r.affinity + $delta
            END
        """, a=char_a, b=char_b, delta=delta)

async def move_location(char_id: str, new_loc_id: str) -> None:
    """캐릭터 장소 이동 공통 로직"""
    async with async_driver.session() as session:
        # 1. 기존 연결 끊기
        await session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(prev:Location)
            DELETE old
            SET prev.current_chars = [x IN prev.current_chars WHERE x <> $char_id]
        """, char_id=char_id)
        # 2. 새 장소 연결
        await session.run("""
            MATCH (c:Character {id: $char_id})
            MATCH (next:Location {id: $new_loc_id})
            MERGE (c)-[:LOCATED_AT]->(next)
            SET next.current_chars = coalesce(next.current_chars, []) + [$char_id]
        """, char_id=char_id, new_loc_id=new_loc_id)
        # 3. DynamicState 업데이트
        await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            SET d.location_id = $new_loc_id
        """, char_id=char_id, new_loc_id=new_loc_id)

async def advance_cycle_day(char_id: str, days: int) -> None:
    """캐릭터 생리/바이오리듬 일자 공통 업데이트"""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            WHERE d.cycle_day IS NOT NULL
            SET d.cycle_day = ((d.cycle_day + $days - 1) % 28) + 1
        """, char_id=char_id, days=days)