# ================================
# src/core/database/records.py
#
# Kuzu query result wrappers that emulate the small Neo4j result surface used by the app.
#
# Classes
#   - KuzuRecord : Row wrapper supporting record["key"], get(), keys(), and bool().
#   - KuzuResult : Eager result wrapper exposing single(), fetch_all(), and data().
# ================================

import kuzu


class KuzuRecord:
    """Wrap one Kuzu result row with dict-like access."""

    def __init__(self, col_names: list[str], values: list) -> None:
        """Store row values by column name."""
        self._data = dict(zip(col_names, values))

    def __getitem__(self, key: str):
        """Return a row value by column name."""
        return self._data[key]

    def keys(self):
        """Return row field names for dict() compatibility."""
        return self._data.keys()

    def get(self, key: str, default=None):
        """Return a row value or default when the key is absent."""
        return self._data.get(key, default)

    def __bool__(self) -> bool:
        """Return whether the row has any fields."""
        return bool(self._data)


class KuzuResult:
    """
    Eagerly materialize a Kuzu QueryResult.

    Kuzu's C++ result object holds resources, so this wrapper reads all rows
    immediately and closes the underlying query result.
    """

    def __init__(self, qr: kuzu.QueryResult) -> None:
        """Read all query rows and release the underlying QueryResult."""
        self._col_names: list[str] = []
        self._rows: list[list] = []
        if qr:
            try:
                self._col_names = qr.get_column_names()
                while qr.has_next():
                    self._rows.append(qr.get_next())
            finally:
                try:
                    qr.close()
                except Exception:
                    pass

    async def single(self) -> KuzuRecord | None:
        """Return the first row, or None when no row exists."""
        if self._rows:
            return KuzuRecord(self._col_names, self._rows[0])
        return None

    async def fetch_all(self) -> list[KuzuRecord]:
        """Return all rows as KuzuRecord objects."""
        return [KuzuRecord(self._col_names, row) for row in self._rows]

    async def data(self) -> list[dict]:
        """Return all rows as dictionaries, matching Neo4j AsyncResult.data()."""
        return [dict(zip(self._col_names, row)) for row in self._rows]
