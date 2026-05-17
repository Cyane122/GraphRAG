# ================================
# src/core/database/session.py
#
# Async Kuzu session wrapper used by runtime graph queries.
#
# Classes
#   - KuzuSession : Async context manager exposing run(query, parameters, **kwargs) -> KuzuResult.
# ================================

import asyncio

import kuzu

from src.core.database.records import KuzuResult


class KuzuSession:
    """
    Provide a Neo4j-like async session interface over a Kuzu connection.

    Existing runtime code can keep using:
        async with async_driver.session() as session:
            result = await session.run(query, key=value)
    """

    def __init__(self, conn: kuzu.Connection, lock: asyncio.Lock) -> None:
        """Bind the session to a shared Kuzu connection and driver lock."""
        self._conn = conn
        self._lock = lock

    async def run(
        self,
        query: str,
        parameters: dict | None = None,
        **kwargs,
    ) -> KuzuResult:
        """Execute a query and return an eager KuzuResult."""
        params = {**(parameters or {}), **kwargs}

        # Kuzu connections are not thread-safe; serialize access per driver.
        async with self._lock:
            qr = await asyncio.to_thread(self._conn.execute, query, params)

        return KuzuResult(qr)

    async def __aenter__(self) -> "KuzuSession":
        """Return this session for async context manager usage."""
        return self

    async def __aexit__(self, *_) -> None:
        """Keep the shared connection open; driver lifecycle owns closing."""
        pass
