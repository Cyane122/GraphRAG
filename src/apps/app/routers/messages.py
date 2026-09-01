# ================================
# src/apps/app/routers/messages.py
#
# Message streaming, reroll, mutation, and variant web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register message routes
# ================================

from __future__ import annotations

import traceback
from collections.abc import AsyncIterator

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from src.apps.app.conversation_ops import get_conversation_ops
from src.apps.app.models import (
    MessageCreateRequest,
    MessageEditRequest,
    MessageRerollRequest,
    VariantActivateRequest,
)
from src.apps.app.routers.shared import RouterContext, _json_line, _load_or_404
from src.apps.app.service import append_user_and_stream


def create_router(context: RouterContext) -> APIRouter:
    """Register message streaming, reroll, and mutation routes."""
    router = APIRouter()
    store = context.store

    @router.post("/api/conversations/{thread_id}/messages/stream")
    async def api_stream_message(thread_id: str, body: MessageCreateRequest) -> StreamingResponse:
        """Append a user message and stream Actor output as NDJSON."""
        try:
            state = store.load(thread_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="conversation not found") from exc

        async def _events() -> AsyncIterator[bytes]:
            """Yield response stream events."""
            try:
                async for event in append_user_and_stream(
                    state,
                    body.content,
                    store,
                    client_message_id=body.client_message_id,
                    actor_model=body.actor_model,
                ):
                    yield _json_line(event)
            except Exception as exc:
                print("[WebStream] generation failed")
                traceback.print_exc()
                yield _json_line({"type": "error", "content": str(exc)})

        return StreamingResponse(_events(), media_type="application/x-ndjson")

    @router.post("/api/conversations/{thread_id}/messages/{assistant_id}/reroll")
    async def api_reroll(
        thread_id: str,
        assistant_id: str,
        body: MessageRerollRequest | None = Body(default=None),
    ) -> dict:
        """Reroll an assistant response."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)
        try:
            result = await ops.reroll(
                state,
                assistant_id,
                store,
                actor_model=body.actor_model if body else None,
            )
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebReroll] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=str(exc)) from exc
        return result

    @router.patch("/api/conversations/{thread_id}/messages/{message_id}/variants/activate")
    async def api_activate_variant(thread_id: str, message_id: str, body: VariantActivateRequest) -> dict:
        """Activate a specific version of an assistant message."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)
        try:
            return await ops.activate(state, message_id, body.version_index, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @router.patch("/api/conversations/{thread_id}/messages/{message_id}")
    async def api_edit_message(thread_id: str, message_id: str, body: MessageEditRequest) -> dict:
        """Edit a user or assistant message."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)
        try:
            return await ops.edit(state, message_id, body.content, store, actor_model=body.actor_model)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, detail=str(exc)) from exc

    @router.delete("/api/conversations/{thread_id}/messages/{message_id}")
    def api_delete_message(thread_id: str, message_id: str) -> dict:
        """Delete a user or assistant message."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)
        try:
            return ops.delete(state, message_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    return router
