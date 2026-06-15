# ================================
# src/ui/web_app/app.py
#
# FastAPI route layer for the standalone GraphRAG web UI.
#
# Functions
#   - create_app() -> FastAPI : Create the standalone web UI FastAPI app.
# ================================

from __future__ import annotations

import json
import traceback
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import WORLD_ID
from src.ui.web_app.models import (
    ConversationCreateRequest,
    LocationMoveRequest,
    MessageCreateRequest,
    MessageEditRequest,
    MessageRerollRequest,
    VariantActivateRequest,
)
from src.ui.web_app.runtime import ActiveConversation, discover_world_profiles, resolve_opening_scene
from src.ui.web_app.service import (
    activate_variant,
    append_user_and_stream,
    create_conversation,
    delete_message,
    edit_message,
    refresh_graph_snapshot_best_effort,
    reroll_assistant,
    run_database_tool,
)
from src.ui.web_app.storage import ConversationStore
from src.ui.web_app.world_state import (
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
            raise HTTPException(500, detail=str(exc)) from exc

    @app.get("/api/conversations/{thread_id}/schema")
    async def api_conversation_schema(thread_id: str) -> dict:
        """Return schema from the graph viewer server when possible."""
        state = _load_or_404(store, thread_id)
        from src.ui.graph_server import _HOST as graph_host
        from src.ui.graph_server import _PORT as graph_port
        from src.ui.graph_server import ensure_graph_server
        from src.ui.graph_loader import get_thread_schema

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
        "messages": [_message_payload(message) for message in state.messages],
    }


app = create_app()
