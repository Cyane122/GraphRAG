# ================================
# src/core/database/helpers.py
#
# Kuzu 공통 쓰기/읽기 헬퍼 함수 모음입니다.
#
# Functions
#   - update_dynamic_state(char_id: str, updates: dict) -> None : DynamicState 노드 속성 공통 업데이트
#   - update_relationship_affinity(char_a: str, char_b: str, delta: int) -> None : 호감도 공통 업데이트 (양방향, ±100 상한)
#   - move_location(char_id: str, new_loc_id: str) -> None : 캐릭터 장소 이동 공통 로직
#   - advance_cycle_day(char_id: str, days: int) -> None : 생리/바이오리듬 일자 공통 업데이트
#   - get_in_universe_time() -> str : GlobalState에서 현재 인게임 시간을 YYYYMMDD_HHMM 형식으로 반환
#   - load_graph_info() -> dict : 그래프 현재 상태(전역·캐릭터·장소·관계)를 dict로 반환
# ================================

from datetime import datetime

from src.core.database.driver import async_driver


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
        # 기존 LOCATED_AT 관계와 이전 Location의 current_chars에서 제거
        await session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(prev:Location)
            DELETE old
            SET prev.current_chars = [x IN prev.current_chars WHERE x <> $char_id]
        """, char_id=char_id)
        # 새 Location으로 이동 — Kuzu는 MERGE 대신 CREATE 사용 (이전 관계를 이미 삭제)
        await session.run("""
            MATCH (c:Character {id: $char_id}), (next:Location {id: $new_loc_id})
            CREATE (c)-[:LOCATED_AT]->(next)
            SET next.current_chars =
                CASE WHEN next.current_chars IS NULL
                THEN [$char_id]
                ELSE next.current_chars + [$char_id]
                END
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
    """GlobalState에서 현재 인게임 시간을 YYYYMMDD_HHMM 형식으로 반환합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await result.single()
        if row and row["ct"]:
            return datetime.fromisoformat(row["ct"]).strftime("%Y%m%d_%H%M")
    return "20240101_0000"


async def load_graph_info() -> dict:
    """
    그래프 현재 상태를 dict로 반환합니다.

    반환 구조:
        global_state : GlobalState 싱글톤 필드
        characters   : 캐릭터별 {id, name, type, dynamic_state} 리스트
        locations    : Location 노드 {id, name, current_chars} 리스트
        relationships: 관계 {from, to, affinity, trust} 리스트
    """
    async with async_driver.session() as session:
        # 1. 전역 상태
        gs_result = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.*"
        )
        gs_row = await gs_result.single()
        global_state = dict(gs_row._data) if gs_row else {}

        # 2. 캐릭터 + 동적 상태
        char_result = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            RETURN c.id AS id, c.name AS name, c.type AS type,
                   d.mood AS mood, d.stress_level AS stress_level,
                   d.physical_condition AS physical_condition,
                   d.mental_condition AS mental_condition,
                   d.location_id AS location_id
        """)
        characters = []
        for row in await char_result.fetch_all():
            characters.append({
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "dynamic_state": {
                    "mood": row["mood"],
                    "stress_level": row["stress_level"],
                    "physical_condition": row["physical_condition"],
                    "mental_condition": row["mental_condition"],
                    "location_id": row["location_id"],
                },
            })

        # 3. 장소
        loc_result = await session.run(
            "MATCH (l:Location) RETURN l.id AS id, l.name AS name, l.current_chars AS current_chars"
        )
        locations = []
        for row in await loc_result.fetch_all():
            locations.append({
                "id": row["id"],
                "name": row["name"],
                "current_chars": row["current_chars"] or [],
            })

        # 4. 관계
        rel_result = await session.run("""
            MATCH (a:Character)-[r:RELATIONSHIP]->(b:Character)
            RETURN a.id AS from_id, b.id AS to_id,
                   r.affinity AS affinity, r.trust AS trust
        """)
        relationships = []
        for row in await rel_result.fetch_all():
            relationships.append({
                "from": row["from_id"],
                "to": row["to_id"],
                "affinity": row["affinity"],
                "trust": row["trust"],
            })

    return {
        "global_state": global_state,
        "characters": characters,
        "locations": locations,
        "relationships": relationships,
    }
