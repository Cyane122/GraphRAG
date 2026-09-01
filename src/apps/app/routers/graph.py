# ================================
# src/apps/app/routers/graph.py
#
# Graph-only database, schema, location, and pregnancy web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register graph-only routes
# ================================

from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from src.apps.app.models import (
    ForcePregnancyRequest,
    LocationMoveRequest,
    SimulatePregnancyRequest,
)
from src.apps.app.routers.shared import RouterContext, _load_or_404, _require_graph_mode
from src.apps.app.runtime import ActiveConversation
from src.apps.app.schema import load_conversation_schema
from src.apps.app.service import (
    force_pregnancy,
    run_database_tool,
    simulate_pregnancy,
)
from src.apps.app.world_state import fetch_location_board, move_character_location


def create_router(context: RouterContext) -> APIRouter:
    """Register graph-only database and world-state routes."""
    router = APIRouter()
    store = context.store

    @router.post("/api/conversations/{thread_id}/tools/{tool_name}")
    async def api_database_tool(thread_id: str, tool_name: str) -> dict:
        """Run a read-only database tool and persist the result as an assistant message."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        try:
            return await run_database_tool(state, tool_name, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebEdit] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=str(exc)) from exc

    @router.get("/api/conversations/{thread_id}/schema")
    async def api_conversation_schema(thread_id: str) -> dict:
        """Return schema from the graph viewer server when possible."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        return await load_conversation_schema(state)

    @router.get("/api/conversations/{thread_id}/locations")
    async def api_conversation_locations(thread_id: str) -> dict:
        """Return the active conversation location board."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        async with ActiveConversation(state):
            return await fetch_location_board()

    @router.patch("/api/conversations/{thread_id}/locations/move")
    async def api_move_character_location(thread_id: str, body: LocationMoveRequest) -> dict:
        """Move a character to another location in the active conversation graph."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        async with ActiveConversation(state):
            try:
                return await move_character_location(body.character_id, body.location_id)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc

    @router.post("/api/conversations/{thread_id}/pregnancy/force")
    async def api_force_pregnancy(thread_id: str, body: ForcePregnancyRequest) -> dict:
        """Force the mother pregnant by the optional father (확률 무시, 강제 임신)."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        try:
            return await force_pregnancy(state, body.mother_id, body.father_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail="character not found") from exc

    @router.post("/api/conversations/{thread_id}/pregnancy/simulate")
    async def api_simulate_pregnancy(thread_id: str, body: SimulatePregnancyRequest) -> dict:
        """Simulate N internal ejaculations on the mother and apply conception if rolled."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        try:
            return await simulate_pregnancy(state, body.mother_id, body.father_id, body.shots, store)
        except KeyError as exc:
            raise HTTPException(404, detail="character not found") from exc

    return router
