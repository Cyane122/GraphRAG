# ================================
# src/ui/web_app/world_state.py
#
# Standalone web UI schema and character-location state helpers.
#
# Functions
#   - fetch_current_schema(world_id: str | None = None, scenario_id: str | None = None) -> list[dict[str, Any]] : Return active Kuzu table schema metadata.
#   - fetch_world_definition_schema(world_id: str, scenario_id: str | None) -> list[dict[str, Any]] : Return schema rebuilt from world definitions.
#   - fetch_location_board() -> dict[str, Any] : Return locations and current character placements.
#   - move_character_location(character_id: str, location_id: str) -> dict[str, Any] : Move a character and return refreshed placements.
# ================================

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import kuzu

from src.agents.manager.world_loader import load_world_instance
from src.assets.worlds.base import apply_schedule_templates
from src.core.database import async_driver, move_location


def _clean(value: Any) -> Any:
    """Convert Kuzu values into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean(item) for item in value]
    try:
        return {str(key): _clean(item) for key, item in dict(value).items()}
    except (TypeError, ValueError):
        return str(value)


def _safe_table_name(value: Any) -> str:
    """Return a table name safe for CALL table_info interpolation."""
    name = str(value or "")
    return "".join(ch for ch in name if ch.isalnum() or ch == "_")


def _schema_from_sync_connection(conn: kuzu.Connection) -> list[dict[str, Any]]:
    """Return table schema metadata from an open synchronous Kuzu connection."""
    tables = _sync_rows(conn, "CALL show_tables() RETURN name, type, comment")
    result: list[dict[str, Any]] = []
    for table in tables:
        table_name = _safe_table_name(table.get("name"))
        if not table_name:
            continue
        columns = _sync_rows(conn, f"CALL table_info('{table_name}') RETURN *")
        result.append(
            {
                "name": table_name,
                "type": str(table.get("type") or ""),
                "comment": str(table.get("comment") or ""),
                "columns": [
                    {"name": str(column.get("name") or ""), "type": str(column.get("type") or "")}
                    for column in columns
                    if column.get("name")
                ],
            }
        )
    return sorted(result, key=lambda item: (str(item.get("type") or ""), str(item.get("name") or "")))


def _sync_rows(conn: kuzu.Connection, query: str) -> list[dict[str, Any]]:
    """Run a synchronous Kuzu query and return dictionaries."""
    qr = conn.execute(query)
    try:
        col_names = qr.get_column_names()
        rows: list[dict[str, Any]] = []
        while qr.has_next():
            rows.append({name: _clean(value) for name, value in zip(col_names, qr.get_next())})
        return rows
    finally:
        try:
            qr.close()
        except Exception:
            pass


def fetch_world_definition_schema(world_id: str, scenario_id: str | None) -> list[dict[str, Any]]:
    """Build a temporary world DB and return its schema without touching live data."""
    with tempfile.TemporaryDirectory(prefix="graphrag_schema_") as temp_dir:
        db_path = Path(temp_dir) / "schema"
        db = kuzu.Database(str(db_path))
        conn = kuzu.Connection(db)
        try:
            world = load_world_instance(world_id, scenario_id or "default")
            world.build_schema(conn, scenario_id or "default")
            apply_schedule_templates(conn, world_id, scenario_id or "default")
            return _schema_from_sync_connection(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass


async def fetch_current_schema(world_id: str | None = None, scenario_id: str | None = None) -> list[dict[str, Any]]:
    """Return active Kuzu table schema metadata, falling back to the world definition when the live DB is locked."""
    async with async_driver.session() as session:
        try:
            tables_result = await session.run("CALL show_tables() RETURN name, type, comment")
        except RuntimeError:
            if not world_id:
                raise
            return fetch_world_definition_schema(world_id, scenario_id)
        tables = await tables_result.data()

        result: list[dict[str, Any]] = []
        for table in tables:
            table_name = _safe_table_name(table.get("name"))
            if not table_name:
                continue
            columns_result = await session.run(f"CALL table_info('{table_name}') RETURN *")
            columns = await columns_result.data()
            result.append(
                {
                    "name": table_name,
                    "type": str(table.get("type") or ""),
                    "comment": str(table.get("comment") or ""),
                    "columns": [
                        {"name": str(column.get("name") or ""), "type": str(column.get("type") or "")}
                        for column in columns
                        if column.get("name")
                    ],
                }
            )
    return sorted(result, key=lambda item: (str(item.get("type") or ""), str(item.get("name") or "")))


async def fetch_location_board() -> dict[str, Any]:
    """Return locations and current character placements."""
    async with async_driver.session() as session:
        global_result = await session.run(
            """
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentLocationId AS current_location_id,
                   gs.currentTime AS current_time
            """
        )
        global_row = await global_result.single()

        location_result = await session.run(
            """
            MATCH (l:Location)
            OPTIONAL MATCH (parent:Location)<-[:PART_OF]-(l)
            RETURN l.id AS id,
                   l.name AS name,
                   l.summary AS summary,
                   parent.id AS parent_id
            """
        )
        locations = [
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or ""),
                "summary": str(row.get("summary") or ""),
                "parent_id": str(row.get("parent_id") or ""),
            }
            for row in await location_result.data()
            if row.get("id")
        ]

        character_result = await session.run(
            """
            MATCH (c:Character)
            OPTIONAL MATCH (c)-[:LOCATED_AT]->(l:Location)
            OPTIONAL MATCH (c)-[:HAS_STATE]->(d:DynamicState)
            RETURN c.id AS id,
                   c.name AS name,
                   c.type AS type,
                   l.id AS location_id,
                   l.name AS location_name,
                   d.location_id AS state_location_id,
                   d.mood AS mood,
                   d.current_task AS current_task
            """
        )
        characters = []
        for row in await character_result.data():
            char_id = str(row.get("id") or "")
            if not char_id:
                continue
            location_id = str(row.get("location_id") or row.get("state_location_id") or "")
            characters.append(
                {
                    "id": char_id,
                    "name": str(row.get("name") or char_id),
                    "type": str(row.get("type") or "Character"),
                    "location_id": location_id,
                    "location_name": str(row.get("location_name") or location_id),
                    "state_location_id": str(row.get("state_location_id") or ""),
                    "mood": str(row.get("mood") or ""),
                    "current_task": str(row.get("current_task") or ""),
                }
            )

    known_location_ids = {location["id"] for location in locations}
    for character in characters:
        location_id = character.get("location_id") or ""
        if location_id and location_id not in known_location_ids:
            locations.append({"id": location_id, "name": location_id, "summary": "", "parent_id": ""})
            known_location_ids.add(location_id)

    return {
        "current_location_id": str(global_row.get("current_location_id") or "") if global_row else "",
        "current_time": str(global_row.get("current_time") or "") if global_row else "",
        "locations": sorted(locations, key=lambda item: (item.get("name") or item.get("id") or "")),
        "characters": sorted(characters, key=lambda item: (item.get("name") or item.get("id") or "")),
    }


async def _character_exists(character_id: str) -> bool:
    """Character 노드 존재 여부를 확인한다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {id: $cid}) RETURN c.id AS id", cid=character_id
        )
        return await rec.single() is not None


async def move_character_location(character_id: str, location_id: str) -> dict[str, Any]:
    """Move a character to a location and return refreshed placements.

    무효 캐릭터/위치는 조용히 무시하지 않고 ValueError를 던져 호출처(API)가 400으로 변환하게 한다.
    """
    if not await _character_exists(character_id):
        raise ValueError(f"unknown character id: {character_id}")
    if not await move_location(character_id, location_id):
        raise ValueError(f"unknown location id: {location_id}")
    return await fetch_location_board()
