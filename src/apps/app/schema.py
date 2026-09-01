# ================================
# src/apps/app/schema.py
#
# Graph conversation schema retrieval with viewer and live-state fallbacks.
#
# Functions
#   - load_conversation_schema(state: ConversationState) -> dict : Return graph schema using the existing fallback order
# ================================

from __future__ import annotations

from src.apps.app.models import ConversationState
from src.apps.app.runtime import ActiveConversation
from src.apps.app.service import refresh_graph_snapshot_best_effort
from src.apps.app.world_state import (
    fetch_current_schema,
    fetch_world_definition_schema,
)
from src.apps.graph_viewer.loader import get_thread_schema
from src.apps.graph_viewer.server import ensure_graph_server, server_address


async def load_conversation_schema(state: ConversationState) -> dict:
    """Return graph schema using the viewer cache, live graph, then world definition."""
    ensure_graph_server()
    graph_host, graph_port = server_address()
    graph_server = f"http://{graph_host}:{graph_port}"
    graph_schema_url = f"{graph_server}/api/schema?threadId={state.thread_id}"
    await refresh_graph_snapshot_best_effort(state)
    schema = get_thread_schema(state.thread_id)
    if schema:
        return {
            "schema": schema,
            "source": "graph_viewer",
            "viewer_url": f"{graph_server}/",
            "schema_url": graph_schema_url,
        }
    try:
        async with ActiveConversation(state):
            return {
                "schema": await fetch_current_schema(state.world_id, state.scenario_id),
                "source": "live",
                "viewer_url": f"{graph_server}/",
                "schema_url": graph_schema_url,
            }
    except RuntimeError as exc:
        if "Could not set lock" not in str(exc):
            raise
        return {
            "schema": fetch_world_definition_schema(state.world_id, state.scenario_id),
            "source": "world_definition",
            "viewer_url": f"{graph_server}/",
            "schema_url": graph_schema_url,
        }
