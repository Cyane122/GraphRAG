# ================================
# src/apps/app/app.py
#
# FastAPI route layer for the standalone GraphRAG web UI.
#
# Functions
#   - create_app() -> FastAPI : Create the standalone web UI FastAPI app.
#   - _load_or_404(store: ConversationStore, thread_id: str) : 스레드 로드 또는 HTTP 404.
#   - _message_payload(message) -> dict : 메시지 모델을 프런트엔드 JSON으로 변환.
#   - _conversation_summary(state) -> dict : 대화 목록 메타데이터 반환.
#   - _conversation_payload(state) -> dict : 대화 전체 페이로드 반환.
# ================================

from __future__ import annotations

import json
import traceback
from pathlib import Path
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import WORLD_ID
from src.apps.app.models import (
    ConversationCreateRequest,
    LocationMoveRequest,
    MessageCreateRequest,
    MessageEditRequest,
    MessageRerollRequest,
    OocConfigRequest,
    UserNoteCreateRequest,
    UserNoteUpdateRequest,
    VariantActivateRequest,
)
from src.apps.app.runtime import ActiveConversation, discover_world_profiles, resolve_opening_scene
from src.apps.app.service import (
    activate_variant,
    append_user_and_stream,
    create_conversation,
    delete_message,
    edit_message,
    refresh_graph_snapshot_best_effort,
    reroll_assistant,
    run_database_tool,
)
from src.apps.app.storage import ConversationStore
from src.apps.app.world_state import (
    fetch_current_schema,
    fetch_location_board,
    fetch_world_definition_schema,
    move_character_location,
)

_APP_DIR = Path(__file__).resolve().parents[3] / "frontend" / "app"


def _json_line(payload: dict) -> bytes:
    """Encode one newline-delimited JSON event."""
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def create_app() -> FastAPI:
    """Create the standalone GraphRAG web UI FastAPI app."""
    app = FastAPI(title="GraphRAG Web UI", docs_url="/api/docs")
    store = ConversationStore()

    @app.get("/")
    def index() -> FileResponse:
        """Serve the standalone frontend entrypoint."""
        return FileResponse(_APP_DIR / "index.html")

    @app.get("/api/worlds")
    def api_worlds() -> dict:
        """Return selectable world/scenario profiles."""
        return {"worlds": discover_world_profiles()}

    @app.get("/api/opening-scene")
    def api_opening_scene(
        world_id: str = Query(...),
        scenario_id: str | None = Query(default=None),
    ) -> dict:
        """Return the opening scene for a world/scenario without creating a thread."""
        return {
            "world_id": world_id,
            "scenario_id": scenario_id or "default",
            "opening_scene": resolve_opening_scene(world_id, scenario_id or "default"),
        }

    @app.post("/api/conversations")
    def api_create_conversation(body: ConversationCreateRequest) -> dict:
        """Create a new conversation."""
        state = create_conversation(body.world_id or WORLD_ID, body.scenario_id, store, actor_model=body.actor_model)
        return _conversation_payload(state)

    @app.get("/api/conversations")
    def api_list_conversations() -> dict:
        """List saved conversations."""
        return {"conversations": [_conversation_summary(state) for state in store.list()]}

    @app.get("/api/conversations/{thread_id}")
    def api_get_conversation(thread_id: str) -> dict:
        """Return one saved conversation."""
        try:
            state = store.load(thread_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="conversation not found") from exc
        return _conversation_payload(state)

    @app.post("/api/conversations/{thread_id}/messages/stream")
    async def api_stream_message(thread_id: str, body: MessageCreateRequest) -> StreamingResponse:
        """Append a user message and stream Actor output as NDJSON."""
        try:
            state = store.load(thread_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="conversation not found") from exc

        async def _events():
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

    @app.post("/api/conversations/{thread_id}/messages/{assistant_id}/reroll")
    async def api_reroll(
        thread_id: str,
        assistant_id: str,
        body: MessageRerollRequest | None = Body(default=None),
    ) -> dict:
        """Reroll an assistant response."""
        state = _load_or_404(store, thread_id)
        try:
            result = await reroll_assistant(state, assistant_id, store, actor_model=body.actor_model if body else None)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebReroll] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=str(exc)) from exc
        return result

    @app.post("/api/conversations/{thread_id}/tools/{tool_name}")
    async def api_database_tool(thread_id: str, tool_name: str) -> dict:
        """Run a read-only database tool and persist the result as an assistant message."""
        state = _load_or_404(store, thread_id)
        try:
            return await run_database_tool(state, tool_name, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except RuntimeError as exc:
            print("[WebEdit] generation failed")
            traceback.print_exc()
            raise HTTPException(500, detail=str(exc)) from exc

    @app.get("/api/conversations/{thread_id}/schema")
    async def api_conversation_schema(thread_id: str) -> dict:
        """Return schema from the graph viewer server when possible."""
        state = _load_or_404(store, thread_id)
        from src.apps.graph_viewer.server import _HOST as graph_host
        from src.apps.graph_viewer.server import _PORT as graph_port
        from src.apps.graph_viewer.server import ensure_graph_server
        from src.apps.graph_viewer.loader import get_thread_schema

        ensure_graph_server()
        graph_server = f"http://{graph_host}:{graph_port}"
        graph_schema_url = f"{graph_server}/api/schema?threadId={thread_id}"
        await refresh_graph_snapshot_best_effort(state)
        schema = get_thread_schema(thread_id)
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

    @app.get("/api/conversations/{thread_id}/locations")
    async def api_conversation_locations(thread_id: str) -> dict:
        """Return the active conversation location board."""
        state = _load_or_404(store, thread_id)
        async with ActiveConversation(state):
            return await fetch_location_board()

    @app.patch("/api/conversations/{thread_id}/locations/move")
    async def api_move_character_location(thread_id: str, body: LocationMoveRequest) -> dict:
        """Move a character to another location in the active conversation graph."""
        state = _load_or_404(store, thread_id)
        async with ActiveConversation(state):
            try:
                return await move_character_location(body.character_id, body.location_id)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc

    @app.patch("/api/conversations/{thread_id}/ooc-config")
    def api_update_ooc_config(thread_id: str, body: OocConfigRequest) -> dict:
        """Update the thread-level OOC config."""
        state = _load_or_404(store, thread_id)
        state.ooc_config = body.ooc_config
        store.save(state)
        return {"ooc_config": state.ooc_config}

    @app.get("/api/conversations/{thread_id}/usernotes")
    def api_list_usernotes(thread_id: str) -> dict:
        """Return all usernotes for a conversation."""
        state = _load_or_404(store, thread_id)
        return {"usernotes": state.usernotes}

    @app.post("/api/conversations/{thread_id}/usernotes")
    def api_create_usernote(thread_id: str, body: UserNoteCreateRequest) -> dict:
        """Create a new usernote."""
        state = _load_or_404(store, thread_id)
        note = {"id": uuid4().hex, "name": body.name, "content": body.content, "enabled": True}
        state.usernotes.append(note)
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @app.patch("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_update_usernote(thread_id: str, note_id: str, body: UserNoteUpdateRequest) -> dict:
        """Update an existing usernote."""
        state = _load_or_404(store, thread_id)
        note = next((n for n in state.usernotes if n["id"] == note_id), None)
        if note is None:
            raise HTTPException(404, detail="usernote not found")
        if body.name is not None:
            note["name"] = body.name
        if body.content is not None:
            note["content"] = body.content
        if body.enabled is not None:
            note["enabled"] = body.enabled
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @app.delete("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_delete_usernote(thread_id: str, note_id: str) -> dict:
        """Delete a usernote."""
        state = _load_or_404(store, thread_id)
        original_len = len(state.usernotes)
        state.usernotes = [n for n in state.usernotes if n["id"] != note_id]
        if len(state.usernotes) == original_len:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"usernotes": state.usernotes}

    @app.patch("/api/conversations/{thread_id}/messages/{message_id}/variants/activate")
    def api_activate_variant(thread_id: str, message_id: str, body: VariantActivateRequest) -> dict:
        """Activate a specific version of an assistant message."""
        state = _load_or_404(store, thread_id)
        try:
            return activate_variant(state, message_id, body.version_index, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @app.patch("/api/conversations/{thread_id}/messages/{message_id}")
    async def api_edit_message(thread_id: str, message_id: str, body: MessageEditRequest) -> dict:
        """Edit a user or assistant message."""
        state = _load_or_404(store, thread_id)
        try:
            return await edit_message(state, message_id, body.content, store, actor_model=body.actor_model)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(500, detail=str(exc)) from exc

    @app.delete("/api/conversations/{thread_id}/messages/{message_id}")
    def api_delete_message(thread_id: str, message_id: str) -> dict:
        """Delete a user or assistant message."""
        state = _load_or_404(store, thread_id)
        try:
            return delete_message(state, message_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    app.mount("/", StaticFiles(directory=_APP_DIR), name="static")
    return app


def _load_or_404(store: ConversationStore, thread_id: str):
    """Load a conversation or raise HTTP 404."""
    try:
        return store.load(thread_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="conversation not found") from exc


def _message_payload(message) -> dict:
    """Convert a message model into frontend JSON."""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "createdAt": message.created_at.strftime("%H:%M"),
        "parentUserId": message.parent_user_id,
        "edited": message.edited,
        "actorModel": message.actor_model,
        "oocConfig": getattr(message, "ooc_config", ""),
        "variants": [
            {
                "id": variant.id,
                "content": variant.content,
                "createdAt": variant.created_at.strftime("%H:%M"),
                "actorModel": variant.actor_model,
                "edited": variant.edited,
            }
            for variant in message.variants
        ],
    }


def _conversation_summary(state) -> dict:
    """Return compact conversation list metadata."""
    return {
        "thread_id": state.thread_id,
        "world_id": state.world_id,
        "scenario_id": state.scenario_id,
        "title": state.title,
        "preview": state.preview,
        "updated_at": state.updated_at.isoformat(),
        "actor_model": state.actor_model,
    }


def _conversation_payload(state) -> dict:
    """Return full conversation payload for the frontend."""
    return {
        **_conversation_summary(state),
        "ooc_config": getattr(state, "ooc_config", ""),
        "usernotes": getattr(state, "usernotes", []),
        "messages": [_message_payload(message) for message in state.messages],
    }


app = create_app()
