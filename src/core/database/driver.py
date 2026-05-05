# ================================
# src/core/database/driver.py
#
# Kuzu 데이터베이스 드라이버. Neo4j async session 인터페이스와 호환되는 래퍼를 제공하여
# 기존 코드(async with async_driver.session() as session: await session.run(...))를
# 수정 없이 재사용할 수 있게 합니다.
#
# Classes
#   - KuzuRecord  : record["key"] 접근을 지원하는 행 래퍼
#   - KuzuResult  : single() / fetch_all() 을 제공하는 결과 래퍼
#   - KuzuSession : Neo4j AsyncSession과 동일한 인터페이스
#   - KuzuAsyncDriver : session() 팩토리 및 동기 execute_sync() 제공
#
# (module-level)
#   - async_driver : WORLD_ID 기반 Kuzu DB를 가리키는 KuzuAsyncDriver 싱글톤
# ================================

import asyncio
import os
from pathlib import Path

import kuzu
from dotenv import load_dotenv
from kuzu import QueryResult

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")


class KuzuRecord:
    """Kuzu 결과 행을 dict처럼 접근할 수 있게 감쌉니다."""

    def __init__(self, col_names: list[str], values: list) -> None:
        self._data = dict(zip(col_names, values))

    def __getitem__(self, key: str):
        return self._data[key]

    def get(self, key: str, default=None):
        """키가 없으면 default를 반환합니다."""
        return self._data.get(key, default)

    def __bool__(self) -> bool:
        return bool(self._data)


class KuzuResult:
    """Kuzu QueryResult를 Neo4j AsyncResult와 유사한 인터페이스로 감쌉니다."""

    def __init__(self, qr: kuzu.QueryResult) -> None:
        self._qr = qr
        self._col_names: list[str] = qr.get_column_names() if qr else []

    async def single(self) -> KuzuRecord | None:
        """첫 번째 행을 KuzuRecord로 반환하고, 결과가 없으면 None을 반환합니다."""
        if self._qr and self._qr.has_next():
            return KuzuRecord(self._col_names, self._qr.get_next())
        return None

    async def fetch_all(self) -> list[KuzuRecord]:
        """모든 행을 KuzuRecord 리스트로 반환합니다."""
        records = []
        while self._qr and self._qr.has_next():
            records.append(KuzuRecord(self._col_names, self._qr.get_next()))
        return records


class KuzuSession:
    """
    Neo4j AsyncSession과 동일한 async context manager + run() 인터페이스를 제공합니다.

    기존 코드 패턴:
        async with async_driver.session() as session:
            await session.run(query, key=value)
    가 수정 없이 동작합니다.
    """

    def __init__(self, conn: kuzu.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    async def run(
        self,
        query: str,
        parameters: dict | None = None,
        **kwargs,
    ) -> KuzuResult:
        """쿼리를 실행하고 KuzuResult를 반환합니다."""
        params = {**(parameters or {}), **kwargs}

        async with self._lock:
            qr = await asyncio.to_thread(self._conn.execute, query, params)

        return KuzuResult(qr)

    async def __aenter__(self) -> "KuzuSession":
        return self

    async def __aexit__(self, *_) -> None:
        pass


class KuzuAsyncDriver:
    """
    Kuzu 데이터베이스에 대한 async 드라이버.

    런타임(async): session() → KuzuSession.run()
    스키마 초기화(sync): execute_sync() → kuzu.Connection.execute()
    """

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db   = kuzu.Database(db_path)
        self._conn = kuzu.Connection(self._db)

    def session(self) -> KuzuSession:
        """KuzuSession을 반환합니다 (async context manager로 사용)."""
        return KuzuSession(self._conn)

    def execute_sync(self, query: str, params: dict | None = None) -> QueryResult | list[QueryResult]:
        """동기 쿼리 실행. 스키마 초기화 CLI 전용."""
        return self._conn.execute(query, params or {})

    async def close(self) -> None:
        """Kuzu는 명시적 close가 필요 없지만 인터페이스 통일을 위해 유지합니다."""
        pass


_world_id = os.getenv("WORLD_ID", "default")
_db_path  = str(Path("graph") / _world_id)

async_driver = KuzuAsyncDriver(_db_path)
