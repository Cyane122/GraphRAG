# ================================
# src/apps/app/schema.py
#
# Read-only Graph conversation schema retrieval with a world-definition fallback.
#
# Functions
#   - load_conversation_schema(state: ConversationState) -> dict : Return graph schema without writing the thread DB
# ================================

from __future__ import annotations

import asyncio
from pathlib import Path

from src.apps.app.models import ConversationState
from src.apps.app.runtime import _ACTIVE_DRIVERS, ActiveConversation, conversation_db_path
from src.apps.app.world_state import (
    fetch_current_schema,
    fetch_thread_schema_readonly,
    fetch_world_definition_schema,
)


async def load_conversation_schema(state: ConversationState) -> dict:
    """Return graph schema without bootstrapping, migrating, or creating the thread DB.

    Order: missing DB -> world definition; driver already open in this process
    -> live schema through it; otherwise a read-only open, falling back to the
    world definition when the DB file is locked.
    """
    # The graph viewer is gone; schema_url points back at this route and
    # viewer_url stays an empty string so clients hide the viewer link.
    links = {
        "viewer_url": "",
        "schema_url": f"/api/conversations/{state.thread_id}/schema",
    }
    definition = {"source": "world_definition", **links}
    db_path = conversation_db_path(state.thread_id)
    if not Path(db_path).exists():
        schema = await asyncio.to_thread(fetch_world_definition_schema, state.world_id, state.scenario_id)
        return {"schema": schema, **definition}
    # A cached driver already ran its bootstrap and migrations when a turn
    # opened it, and it holds the file lock, so reuse it instead of a second open.
    if db_path in _ACTIVE_DRIVERS:
        async with ActiveConversation(state):
            schema = await fetch_current_schema(state.world_id, state.scenario_id)
        return {"schema": schema, "source": "live", **links}
    try:
        schema = await asyncio.to_thread(fetch_thread_schema_readonly, db_path)
    except RuntimeError as exc:
        if "Could not set lock" not in str(exc):
            raise
        schema = await asyncio.to_thread(fetch_world_definition_schema, state.world_id, state.scenario_id)
        return {"schema": schema, **definition}
    return {"schema": schema, "source": "live", **links}
