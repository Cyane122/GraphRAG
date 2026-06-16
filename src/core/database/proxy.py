# ================================
# src/core/database/proxy.py
#
# Runtime proxy that resolves the active Kuzu driver for the current Chainlit session.
#
# Classes
#   - ProxyDriver : Delegates session()/transaction() and execute_sync() to the current session driver or the default driver.
# ================================

from collections.abc import Callable
from typing import Any

from src.core.database.session import KuzuSession, KuzuTransaction


class ProxyDriver:
    """Delegate database calls to a dynamically resolved concrete driver."""

    def __init__(self, resolver: Callable[[], Any]) -> None:
        """Store the active-driver resolver."""
        self._resolver = resolver

    def session(self) -> KuzuSession:
        """Return a session from the active concrete driver."""
        return self._resolver().session()

    def transaction(self) -> KuzuTransaction:
        """Return an atomic transaction from the active concrete driver."""
        return self._resolver().transaction()

    def execute_sync(self, query: str, params: dict | None = None):
        """Execute a synchronous query through the active concrete driver."""
        return self._resolver().execute_sync(query, params)

    async def close(self) -> None:
        """No-op close; concrete drivers own resources."""
        pass
