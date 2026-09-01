# ================================
# src/apps/app/routers/usernotes.py
#
# World-shared user-note web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register user-note routes
# ================================

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from src.apps.app.models import UserNoteCreateRequest, UserNoteUpdateRequest
from src.apps.app.routers.shared import RouterContext, _load_or_404


def create_router(context: RouterContext) -> APIRouter:
    """Register world-shared user-note routes."""
    router = APIRouter()
    store = context.store

    @router.get("/api/conversations/{thread_id}/usernotes")
    def api_list_usernotes(thread_id: str) -> dict:
        """Return usernotes shared by this thread's mode and world."""
        state = _load_or_404(store, thread_id)
        return {"usernotes": state.usernotes}

    @router.post("/api/conversations/{thread_id}/usernotes")
    def api_create_usernote(thread_id: str, body: UserNoteCreateRequest) -> dict:
        """Create a world-shared usernote."""
        state = _load_or_404(store, thread_id)
        note = {"id": uuid4().hex, "name": body.name, "content": body.content, "enabled": True}
        state.usernotes = store.add_world_usernote(state, note)
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @router.patch("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_update_usernote(thread_id: str, note_id: str, body: UserNoteUpdateRequest) -> dict:
        """Update an existing world-shared usernote."""
        state = _load_or_404(store, thread_id)
        changes = body.model_dump(exclude_none=True)
        note, state.usernotes = store.update_world_usernote(state, note_id, changes)
        if note is None:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @router.delete("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_delete_usernote(thread_id: str, note_id: str) -> dict:
        """Delete a world-shared usernote."""
        state = _load_or_404(store, thread_id)
        deleted, state.usernotes = store.delete_world_usernote(state, note_id)
        if not deleted:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"usernotes": state.usernotes}

    return router
