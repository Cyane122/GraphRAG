# ================================
# src/apps/app/routers/usernotes.py
#
# Global usernote library web routes, with enabled state scoped per conversation thread.
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
    """Register global-library user-note routes with per-thread enabled state."""
    router = APIRouter()
    store = context.store

    @router.get("/api/conversations/{thread_id}/usernotes")
    def api_list_usernotes(thread_id: str) -> dict:
        """Return the global usernote library, hydrated with this thread's enabled state."""
        state = _load_or_404(store, thread_id)
        return {"usernotes": state.usernotes}

    @router.post("/api/conversations/{thread_id}/usernotes")
    def api_create_usernote(thread_id: str, body: UserNoteCreateRequest) -> dict:
        """Create a usernote in the global library and enable it for this thread."""
        state = _load_or_404(store, thread_id)
        note = {
            "id": uuid4().hex,
            "name": body.name,
            "content": body.content,
            "enabled": body.enabled,
        }
        state.usernotes = store.add_usernote(state, note)
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @router.patch("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_update_usernote(thread_id: str, note_id: str, body: UserNoteUpdateRequest) -> dict:
        """Update a usernote's library fields and/or this thread's enabled state."""
        state = _load_or_404(store, thread_id)
        changes = body.model_dump(exclude_none=True)
        note, state.usernotes = store.update_usernote(state, note_id, changes)
        if note is None:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @router.delete("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_delete_usernote(thread_id: str, note_id: str) -> dict:
        """Delete a usernote from the global library and from this thread's enabled ids."""
        state = _load_or_404(store, thread_id)
        deleted, state.usernotes = store.delete_usernote(state, note_id)
        if not deleted:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"usernotes": state.usernotes}

    return router
