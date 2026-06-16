# ================================
# src/core/database/session.py
#
# Async Kuzu session/transaction wrappers used by runtime graph queries.
#
# Classes
#   - KuzuSession : Async context manager exposing run(query, parameters, **kwargs) -> KuzuResult (per-query lock).
#   - KuzuTransaction : Async context manager holding the driver lock across BEGIN→COMMIT for atomic multi-write ops.
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


class KuzuTransaction:
    """
    Atomic multi-write context over a single Kuzu connection.

    Unlike KuzuSession (which locks per query and releases between them), this
    holds the driver lock for the whole BEGIN→COMMIT span. That gives three
    properties at once: atomicity (COMMIT on success / ROLLBACK on error), strict
    serialization against other queries on the same connection, and lost-update
    safety for read-modify-write sequences.

    Usage:
        async with async_driver.transaction() as tx:
            row = await (await tx.run(...)).single()
            await tx.run(...)   # commits on clean exit, rolls back on exception

    Caveat: the driver lock is NOT reentrant. Code running inside a transaction
    must issue queries through this `tx`, never via async_driver.session(), or it
    will deadlock on the same lock.
    """

    def __init__(self, conn: kuzu.Connection, lock: asyncio.Lock) -> None:
        """Bind the transaction to a shared Kuzu connection and driver lock."""
        self._conn = conn
        self._lock = lock

    async def _exec(self, statement: str) -> None:
        """Run a transaction control statement (BEGIN/COMMIT/ROLLBACK) and drop its result."""
        qr = await asyncio.to_thread(self._conn.execute, statement, {})
        try:
            qr.close()
        except Exception:
            pass

    async def _rollback_quietly(self) -> None:
        """Roll back without masking an in-flight exception."""
        try:
            await self._exec("ROLLBACK")
        except Exception as exc:
            print(f"[KuzuTransaction] rollback failed (ignored): {exc}")

    async def run(
        self,
        query: str,
        parameters: dict | None = None,
        **kwargs,
    ) -> KuzuResult:
        """Execute a query inside the held transaction and return an eager KuzuResult."""
        params = {**(parameters or {}), **kwargs}
        # Lock is already held by __aenter__ for the whole transaction; do not re-acquire.
        qr = await asyncio.to_thread(self._conn.execute, query, params)
        return KuzuResult(qr)

    async def __aenter__(self) -> "KuzuTransaction":
        """Acquire the driver lock and open the transaction."""
        await self._lock.acquire()
        try:
            await self._exec("BEGIN TRANSACTION")
        except BaseException:
            self._lock.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        """Commit on clean exit, roll back on error; always release the lock."""
        try:
            if exc_type is None:
                try:
                    await self._exec("COMMIT")
                except BaseException:
                    # Commit failed → roll back and surface the commit error.
                    await self._rollback_quietly()
                    raise
            else:
                await self._rollback_quietly()
        finally:
            self._lock.release()
        return False
