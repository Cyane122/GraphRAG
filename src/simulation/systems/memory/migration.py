# ================================
# src/simulation/systems/memory/migration.py
#
# Best-effort schema migration for Memory/Event metadata columns (v1).
#
# Functions
#   - run_memory_migration() -> None : Add v1 metadata columns to Memory and Event tables
# ================================

from src.core.database import async_driver

# New Memory columns: (name, kuzu_type, default_literal)
_MEMORY_COLUMNS: list[tuple[str, str, str]] = [
    ("status",               "STRING",  "'active'"),
    ("source_commit_id",     "STRING",  "''"),
    ("source_type",          "STRING",  "'direct_experience'"),
    ("confidence",           "DOUBLE",  "0.75"),
    ("signals",              "STRING",  "'[]'"),
    ("salience",             "DOUBLE",  "0.0"),
    ("recall_count",         "INT64",   "0"),
    ("last_recalled_at",     "STRING",  "''"),
    ("reinforced_count",     "INT64",   "0"),
    ("last_reinforced_at",   "STRING",  "''"),
    ("resolved_at",          "STRING",  "''"),
]

_EVENT_COLUMNS: list[tuple[str, str, str]] = [
    ("source_commit_id", "STRING", "''"),
]


async def run_memory_migration() -> None:
    """
    Add v1 metadata columns to Memory and Event node tables.
    Best-effort: silently skips columns that already exist.
    Call once per world DB after upgrading to this version.
    """
    async with async_driver.session() as session:
        for col, col_type, default in _MEMORY_COLUMNS:
            try:
                await session.run(
                    f"ALTER TABLE Memory ADD {col} {col_type} DEFAULT {default}"
                )
                print(f"[MemoryMigration] Memory.{col} 추가됨")
            except Exception as exc:
                print(f"[MemoryMigration] Memory.{col} 스킵 ({exc})")

        for col, col_type, default in _EVENT_COLUMNS:
            try:
                await session.run(
                    f"ALTER TABLE Event ADD {col} {col_type} DEFAULT {default}"
                )
                print(f"[MemoryMigration] Event.{col} 추가됨")
            except Exception as exc:
                print(f"[MemoryMigration] Event.{col} 스킵 ({exc})")
