# ================================
# tests/smoke_db_transaction.py
#
# core/database 트랜잭션 계층(KuzuTransaction, update_global_flags, move_location)의
# 원자성·lost-update 방지·롤백을 임시 Kuzu DB로 검증하는 smoke 검사.
#
# Functions
#   - _seed_base_tables(path: str) -> None : 임시 DB에 최소 베이스 스키마/노드를 생성.
#   - _read_flags() -> dict : 현재 GlobalState.flags JSON을 dict로 읽는다.
#   - _check_commit_rollback() -> None : 트랜잭션 commit 반영 / 예외 시 rollback 폐기 검증.
#   - _check_lost_update() -> None : 동시 flags 갱신 두 건이 모두 살아남는지 검증.
#   - _check_move_location() -> None : 무효 위치 False + 유효 이동 시 LOCATED_AT/DynamicState 원자 동기화 검증.
#   - _check_tx_aware_helpers() -> None : tx 합류 헬퍼(move+state)의 원자성·롤백·데드락 회귀 검증.
#   - _run() -> None : 임시 드라이버를 활성화하고 모든 검사를 실행.
#   - main() -> None : 전체 smoke 검사를 실행.
# ================================

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kuzu  # noqa: E402

from src.core.database import async_driver  # noqa: E402
from src.core.database.driver import (  # noqa: E402
    KuzuAsyncDriver,
    reset_active_driver,
    set_active_driver,
)
from src.core.database.helpers import (  # noqa: E402
    move_location,
    set_global_flag,
    update_dynamic_state,
    update_global_flags,
)


def _seed_base_tables(path: str) -> None:
    """임시 DB에 드라이버 부트스트랩을 건너뛰게 할 최소 베이스 스키마/노드를 만든다."""
    db = kuzu.Database(path)
    conn = kuzu.Connection(db)
    conn.execute("CREATE NODE TABLE Character(id STRING, name STRING, PRIMARY KEY(id))")
    conn.execute("CREATE NODE TABLE DynamicState(id STRING, location_id STRING, mood STRING, PRIMARY KEY(id))")
    conn.execute("CREATE NODE TABLE Location(id STRING, name STRING, PRIMARY KEY(id))")
    conn.execute("CREATE NODE TABLE GlobalState(id STRING, flags STRING, PRIMARY KEY(id))")
    conn.execute("CREATE REL TABLE HAS_STATE(FROM Character TO DynamicState)")
    conn.execute("CREATE REL TABLE LOCATED_AT(FROM Character TO Location)")
    conn.execute("CREATE (:GlobalState {id: 'singleton', flags: '{}'})")
    conn.execute("CREATE (:Character {id: 'alice', name: 'Alice'})")
    conn.execute("CREATE (:DynamicState {id: 'alice_ds', location_id: 'home'})")
    conn.execute("MATCH (a:Character {id: 'alice'}), (d:DynamicState {id: 'alice_ds'}) CREATE (a)-[:HAS_STATE]->(d)")
    conn.execute("CREATE (:Location {id: 'home', name: 'Home'})")
    conn.execute("CREATE (:Location {id: 'park', name: 'Park'})")
    conn.execute("MATCH (a:Character {id: 'alice'}), (l:Location {id: 'home'}) CREATE (a)-[:LOCATED_AT]->(l)")
    conn.close()
    db.close()


async def _read_flags() -> dict:
    """현재 GlobalState.flags JSON을 dict로 읽는다."""
    async with async_driver.session() as session:
        row = await (await session.run(
            "MATCH (g:GlobalState {id: 'singleton'}) RETURN g.flags AS f"
        )).single()
    return json.loads(row["f"]) if row and row["f"] else {}


async def _check_commit_rollback() -> None:
    """정상 종료 시 commit, 예외 발생 시 rollback으로 변경이 폐기되는지 검증한다."""
    # commit 경로: 값이 반영된다.
    async with async_driver.transaction() as tx:
        await tx.run(
            "MATCH (g:GlobalState {id: 'singleton'}) SET g.flags = $f",
            f=json.dumps({"x": 1}),
        )
    assert (await _read_flags()) == {"x": 1}

    # rollback 경로: 트랜잭션 안에서 예외가 나면 write가 폐기된다.
    try:
        async with async_driver.transaction() as tx:
            await tx.run(
                "MATCH (g:GlobalState {id: 'singleton'}) SET g.flags = $f",
                f=json.dumps({"x": 999}),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert (await _read_flags()) == {"x": 1}, "rollback이 write를 폐기하지 못했다"


async def _check_lost_update() -> None:
    """동시에 서로 다른 flags 키를 쓰는 두 갱신이 모두 보존되는지 검증한다(직렬화)."""
    await update_global_flags(lambda f: f.clear())
    await asyncio.gather(set_global_flag("a", True), set_global_flag("b", True))
    flags = await _read_flags()
    assert flags.get("a") is True and flags.get("b") is True, f"lost-update 발생: {flags}"


async def _check_move_location() -> None:
    """무효 위치는 False, 유효 이동은 LOCATED_AT/DynamicState를 원자적으로 동기화한다."""
    assert await move_location("alice", "does_not_exist") is False
    assert await move_location("alice", "park") is True

    async with async_driver.session() as session:
        loc = await (await session.run(
            "MATCH (c:Character {id: 'alice'})-[:LOCATED_AT]->(l:Location) RETURN l.id AS id"
        )).single()
        ds = await (await session.run(
            "MATCH (c:Character {id: 'alice'})-[:HAS_STATE]->(d:DynamicState) RETURN d.location_id AS lid"
        )).single()
    assert loc["id"] == "park" and ds["lid"] == "park", (loc["id"], ds["lid"])


async def _check_tx_aware_helpers() -> None:
    """tx를 주면 move_location+update_dynamic_state가 데드락 없이 한 트랜잭션에 합류하고,

    예외 발생 시 둘 다 롤백되는지 검증한다(중첩 헬퍼 락 재진입 데드락 회귀 방지).
    """
    # 시작 상태로 되돌린다(alice → home).
    await move_location("alice", "home")

    # 커밋 경로: 같은 트랜잭션에서 이동 + 상태 갱신이 함께 반영된다(데드락 없이).
    async with async_driver.transaction() as tx:
        assert await move_location("alice", "park", tx=tx) is True
        await update_dynamic_state("alice", {"mood": "great"}, tx=tx)
    async with async_driver.session() as session:
        loc = await (await session.run(
            "MATCH (c:Character {id: 'alice'})-[:LOCATED_AT]->(l:Location) RETURN l.id AS id"
        )).single()
        ds = await (await session.run(
            "MATCH (c:Character {id: 'alice'})-[:HAS_STATE]->(d:DynamicState) RETURN d.location_id AS lid, d.mood AS mood"
        )).single()
    assert loc["id"] == "park" and ds["lid"] == "park" and ds["mood"] == "great", (loc["id"], ds["lid"], ds["mood"])

    # 롤백 경로: 트랜잭션 안에서 예외 → 이동/상태 모두 폐기(park/great 유지).
    try:
        async with async_driver.transaction() as tx:
            await move_location("alice", "home", tx=tx)
            await update_dynamic_state("alice", {"mood": "sad"}, tx=tx)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    async with async_driver.session() as session:
        ds = await (await session.run(
            "MATCH (c:Character {id: 'alice'})-[:HAS_STATE]->(d:DynamicState) RETURN d.location_id AS lid, d.mood AS mood"
        )).single()
    assert ds["lid"] == "park" and ds["mood"] == "great", ("rollback 실패", ds["lid"], ds["mood"])


async def _run() -> None:
    """임시 드라이버를 활성화하고 모든 검사를 순서대로 실행한다."""
    path = os.path.join(tempfile.gettempdir(), "kztx_" + uuid.uuid4().hex)
    _seed_base_tables(path)
    driver = KuzuAsyncDriver(path)
    token = set_active_driver(driver)
    try:
        await _check_commit_rollback()
        await _check_lost_update()
        await _check_move_location()
        await _check_tx_aware_helpers()
    finally:
        reset_active_driver(token)
        driver.close()


def main() -> None:
    """트랜잭션 계층 smoke 검사를 실행한다."""
    asyncio.run(_run())
    print("smoke_db_transaction: ok")


if __name__ == "__main__":
    main()
