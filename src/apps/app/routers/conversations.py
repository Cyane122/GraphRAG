# ================================
# src/apps/app/routers/conversations.py
#
# Conversation creation and retrieval web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register conversation routes
# ================================

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.apps.app.models import ConversationCreateRequest, WorldMode
from src.apps.app.routers.shared import RouterContext, _conversation_payload, _conversation_summary
from src.apps.app.service import create_conversation
from src.config import WORLD_ID


def create_router(context: RouterContext) -> APIRouter:
    """Register conversation creation and retrieval routes."""
    router = APIRouter()
    store = context.store

    @router.post("/api/conversations")
    def api_create_conversation(body: ConversationCreateRequest) -> dict:
        """Create a new conversation."""
        try:
            state = create_conversation(
                body.world_id or WORLD_ID,
                body.scenario_id,
                store,
                actor_model=body.actor_model,
                ooc_config=body.ooc_config,
                world_mode=body.world_mode,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _conversation_payload(state)

    @router.get("/api/conversations")
    def api_list_conversations(world_mode: WorldMode = Query(default="graph")) -> dict:
        """List saved conversations."""
        return {
            "world_mode": world_mode,
            "conversations": [
                _conversation_summary(state)
                for state in store.list()
                if state.world_mode == world_mode
            ],
        }

    @router.get("/api/conversations/{thread_id}")
    def api_get_conversation(thread_id: str) -> dict:
        """Return one saved conversation."""
        try:
            state = store.load(thread_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="conversation not found") from exc
        return _conversation_payload(state)

    return router
