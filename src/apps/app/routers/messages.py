# ================================
# src/apps/app/routers/messages.py
#
# Message streaming, reroll, mutation (including streamed edit), and variant web routes.
#
# Functions
#   - _user_facing_error_message(exc: BaseException) -> str : provider 429/403 한도 소진 예외를 사람이 읽을 수 있는 한국어 문구로 치환합니다(그 외는 원문 유지).
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
from src.core.llm.client import is_retryable_provider_limit

_PROVIDER_LIMIT_MESSAGE = "모델 사용량 한도(429)에 걸렸습니다. 잠시 후 다시 시도해 주세요."


def _user_facing_error_message(exc: BaseException) -> str:
    """예외를 사용자에게 보여줄 한국어 문구로 치환합니다.

    provider 429/403 한도 소진(`is_retryable_provider_limit`)이면 Vertex/Gemini가
    돌려주는 원문 오류 JSON 대신 간결한 한국어 안내를 반환한다. 그 외 예외는 원문
    메시지를 그대로 반환한다. 서버 로그(`traceback.print_exc()`)에는 항상 원문이
    그대로 남으므로 진단 정보는 손실되지 않는다.
    """
    if is_retryable_provider_limit(exc):
        return _PROVIDER_LIMIT_MESSAGE
    return str(exc)


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
                    prose_profile=body.prose_profile,
                    engine_modules=body.engine_modules,
                ):
                    yield _json_line(event)
            except Exception as exc:
                print("[WebStream] generation failed")
                traceback.print_exc()
                yield _json_line({"type": "error", "content": _user_facing_error_message(exc)})

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
                prose_profile=body.prose_profile if body else None,
                engine_modules=body.engine_modules if body else None,
            )
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebReroll] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=_user_facing_error_message(exc)) from exc
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
            return await ops.edit(
                state,
                message_id,
                body.content,
                store,
                actor_model=body.actor_model,
                prose_profile=body.prose_profile,
                engine_modules=body.engine_modules,
            )
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebEdit] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=_user_facing_error_message(exc)) from exc

    @router.post("/api/conversations/{thread_id}/messages/{message_id}/edit/stream")
    async def api_stream_edit_message(
        thread_id: str,
        message_id: str,
        body: MessageEditRequest,
    ) -> StreamingResponse:
        """Edit a message and stream any regenerated Actor output as NDJSON."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)

        async def _events() -> AsyncIterator[bytes]:
            """Yield edit stream events."""
            try:
                async for event in ops.stream_edit(
                    state,
                    message_id,
                    body.content,
                    store,
                    actor_model=body.actor_model,
                    prose_profile=body.prose_profile,
                    engine_modules=body.engine_modules,
                ):
                    yield _json_line(event)
            except Exception as exc:
                print("[WebEditStream] edit failed")
                traceback.print_exc()
                yield _json_line({"type": "error", "content": _user_facing_error_message(exc)})

        return StreamingResponse(_events(), media_type="application/x-ndjson")

    @router.delete("/api/conversations/{thread_id}/messages/{message_id}")
    def api_delete_message(thread_id: str, message_id: str) -> dict:
        """Delete a user or assistant message."""
        state = _load_or_404(store, thread_id)
        ops = get_conversation_ops(state.world_mode)
        try:
            return ops.delete(state, message_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    return router
