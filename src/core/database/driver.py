# ================================
# src/core/database/driver.py
#
# Kuzu 데이터베이스 드라이버. Neo4j async session 인터페이스와 호환되는 래퍼를 제공하여
# 기존 코드(async with async_driver.session() as session: await session.run(...))를
# 수정 없이 재사용할 수 있게 합니다.
#
# Classes
#   - KuzuRecord  : record["key"] 접근 + dict() 변환을 지원하는 행 래퍼
#   - KuzuResult  : single() / fetch_all() 을 제공하는 결과 래퍼
#   - KuzuSession : Neo4j AsyncSession과 동일한 인터페이스
#   - KuzuAsyncDriver : session() 팩토리 및 동기 execute_sync() 제공
#
# (module-level)
#   - async_driver : src.config.WORLD_ID 기반 Kuzu DB를 가리키는 KuzuAsyncDriver 싱글톤
# ================================

import asyncio
from pathlib import Path

import kuzu
from kuzu import QueryResult

from src.config import WORLD_ID

# 스키마 업데이트로 추가된 컬럼이 기존 DB에 없을 수 있으므로 시작 시 마이그레이션 시도
# 테이블 미존재 또는 컬럼 이미 존재 시 예외를 조용히 무시한다 (idempotent)
_COLUMN_MIGRATIONS: list[str] = [
    "ALTER TABLE Event ADD COLUMN safety_impact DOUBLE DEFAULT 0.0",
    "ALTER TABLE Event ADD COLUMN safety_resolved BOOLEAN DEFAULT false",
    "ALTER TABLE Event ADD COLUMN safety_decay_rate DOUBLE DEFAULT 0.002",
]


class KuzuRecord:
    """Kuzu 결과 행을 dict처럼 접근할 수 있게 감쌉니다."""

    def __init__(self, col_names: list[str], values: list) -> None:
        self._data = dict(zip(col_names, values))

    def __getitem__(self, key: str):
        return self._data[key]

    def keys(self):
        """dict() 변환 호환을 위해 키 목록을 반환합니다."""
        return self._data.keys()

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

    async def data(self) -> list[dict]:
        """모든 행을 dict 리스트로 반환합니다. Neo4j AsyncResult.data()와 호환."""
        records = []
        while self._qr and self._qr.has_next():
            records.append(dict(zip(self._col_names, self._qr.get_next())))
        return records


class KuzuSession:
    """
    Neo4j AsyncSession과 동일한 async context manager + run() 인터페이스를 제공합니다.

    기존 코드 패턴:
        async with async_driver.session() as session:
            await session.run(query, key=value)
    가 수정 없이 동작합니다.
    """

    def __init__(self, conn: kuzu.Connection, lock: asyncio.Lock) -> None:
        self._conn = conn
        self._lock = lock

    async def run(
        self,
        query: str,
        parameters: dict | None = None,
        **kwargs,
    ) -> KuzuResult:
        """쿼리를 실행하고 KuzuResult를 반환합니다."""
        params = {**(parameters or {}), **kwargs}

        # 드라이버 전역 락으로 직렬화 — kuzu.Connection은 스레드 안전하지 않음
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
        self._lock = asyncio.Lock()
        self._run_migrations()

    def session(self) -> KuzuSession:
        """KuzuSession을 반환합니다 (async context manager로 사용)."""
        return KuzuSession(self._conn, self._lock)

    def _run_migrations(self) -> None:
        """기존 DB에 누락된 컬럼을 추가한다. 이미 존재하거나 테이블 미존재 시 무시."""
        for ddl in _COLUMN_MIGRATIONS:
            try:
                self._conn.execute(ddl)
            except Exception:
                pass

    def execute_sync(self, query: str, params: dict | None = None) -> QueryResult | list[QueryResult]:
        """동기 쿼리 실행. 스키마 초기화 CLI 전용."""
        return self._conn.execute(query, params or {})

    async def close(self) -> None:
        """Kuzu는 명시적 close가 필요 없지만 인터페이스 통일을 위해 유지합니다."""
        pass


_db_path  = str(Path("graph") / WORLD_ID)

async_driver = KuzuAsyncDriver(_db_path)
