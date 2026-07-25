# ================================
# src/apps/app/app.py
#
# FastAPI route layer for the standalone GraphRAG web UI.
#
# Functions
#   - create_app() -> FastAPI : Create the standalone web UI FastAPI app.
#   - _load_or_404(store: ConversationStore, thread_id: str) -> ConversationState : 스레드 로드 또는 HTTP 404.
#   - _require_graph_mode(state: ConversationState) -> None : Reject graph-only tools for Wiki threads.
#   - _require_wiki_mode(state: ConversationState) -> None : Reject Wiki controls for Graph threads.
#   - _conversation_summary(state) -> dict : 대화 목록 메타데이터 반환.
#   - _conversation_payload(state) -> dict : 대화 전체 페이로드 반환.
# ================================

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import AsyncIterator
from ipaddress import ip_address
from pathlib import Path
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.config import HOSTED_UI_ORIGINS, WORLD_ID
from src.apps.app.models import (
    AppSettingsRequest,
    ConversationCreateRequest,
    ConversationState,
    LocationMoveRequest,
    MessageCreateRequest,
    MessageEditRequest,
    MessageRerollRequest,
    OocConfigRequest,
    ForcePregnancyRequest,
    SimulatePregnancyRequest,
    UserNoteCreateRequest,
    UserNoteUpdateRequest,
    VariantActivateRequest,
    WikiCommitSkipRequest,
    WikiConversationArchiveRequest,
    WikiConversationRenameRequest,
    WorldMode,
)
from src.apps.app.conversation_lifecycle import (
    delete_wiki_conversation,
    export_wiki_conversation,
    rename_wiki_conversation,
    set_wiki_conversation_archived,
)
from src.apps.app.settings import load_settings, save_settings
from src.apps.app.runtime import ActiveConversation, discover_world_profiles, resolve_opening_scene
from src.apps.app.message_ops import (
    activate_variant,
    delete_message,
    edit_message,
    reroll_assistant,
)
from src.apps.app.live_console import configure_live_console, get_live_console
from src.apps.app.service import (
    _message_payload,
    append_user_and_stream,
    create_conversation,
    force_pregnancy,
    refresh_graph_snapshot_best_effort,
    run_database_tool,
    simulate_pregnancy,
)
from src.apps.app.storage import ConversationStore
from src.apps.app.wiki_branching import branch_wiki_conversation_before_message
from src.apps.app.wiki_controls import (
    apply_wiki_commit_inverse,
    apply_wiki_commit_now,
    apply_wiki_thread_migration,
    get_wiki_commit_status,
    get_wiki_diagnostics,
    get_wiki_document_list,
    get_wiki_manual_audit,
    get_wiki_thread_migration,
    plan_wiki_commit_inverse,
    regenerate_wiki_update,
    record_wiki_manual_audit,
    retry_wiki_update,
    skip_wiki_commit,
)
from src.apps.app.wiki_message_ops import (
    activate_wiki_variant,
    delete_wiki_message,
    edit_wiki_message,
    reroll_wiki_assistant,
)
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
    configure_live_console()
    app = FastAPI(title="GraphRAG Web UI", docs_url="/api/docs")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(HOSTED_UI_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        allow_private_network=True,
    )
    store = ConversationStore()

    @app.get("/")
    def index() -> FileResponse:
        """Serve the standalone frontend entrypoint."""
        return FileResponse(_APP_DIR / "index.html")

    @app.get("/api/worlds")
    def api_worlds(world_mode: WorldMode = Query(default="graph", alias="mode")) -> dict:
        """Return selectable world/scenario profiles."""
        return {"mode": world_mode, "worlds": discover_world_profiles(world_mode)}

    @app.get("/api/console/stream")
    async def api_console_stream(
        request: Request,
        after: int = Query(default=0, ge=0),
        instance_id: str | None = Query(default=None),
    ) -> StreamingResponse:
        """Stream recent and newly emitted process logs as NDJSON."""
        client_host = request.client.host if request.client else ""
        try:
            is_loopback = ip_address(client_host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise HTTPException(403, detail="console stream is available only from this device")

        console = get_live_console()

        async def _events() -> AsyncIterator[bytes]:
            """Yield buffered console lines and lightweight keepalive events."""
            latest_seq = console.latest_seq()
            cursor = 0 if instance_id != console.instance_id or after > latest_seq else after
            idle_ticks = 0
            yield _json_line(
                {
                    "type": "ready",
                    "instance_id": console.instance_id,
                    "latest_seq": latest_seq,
                }
            )
            while not await request.is_disconnected():
                entries = console.entries_after(cursor)
                if entries:
                    idle_ticks = 0
                    for entry in entries:
                        cursor = entry.seq
                        yield _json_line({"type": "log", **entry.model_dump()})
                else:
                    idle_ticks += 1
                    if idle_ticks >= 40:
                        idle_ticks = 0
                        yield _json_line({"type": "keepalive"})
                await asyncio.sleep(0.25)

        return StreamingResponse(
            _events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/opening-scene")
    def api_opening_scene(
        world_id: str = Query(...),
        scenario_id: str | None = Query(default=None),
        world_mode: WorldMode = Query(default="graph", alias="mode"),
    ) -> dict:
        """Return the opening scene for a world/scenario without creating a thread."""
        return {
            "world_id": world_id,
            "world_mode": world_mode,
            "scenario_id": scenario_id or "default",
            "opening_scene": resolve_opening_scene(world_id, scenario_id or "default", world_mode),
        }

    @app.post("/api/conversations")
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

    @app.get("/api/conversations")
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

    @app.get("/api/conversations/{thread_id}")
    def api_get_conversation(thread_id: str) -> dict:
        """Return one saved conversation."""
        try:
            state = store.load(thread_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="conversation not found") from exc
        return _conversation_payload(state)

    @app.patch("/api/conversations/{thread_id}/wiki/title")
    def api_rename_wiki_conversation(
        thread_id: str,
        body: WikiConversationRenameRequest,
    ) -> dict:
        """Change one Wiki conversation's user-facing title."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            renamed = rename_wiki_conversation(state, body.title, store)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _conversation_payload(renamed)

    @app.patch("/api/conversations/{thread_id}/wiki/archive")
    def api_archive_wiki_conversation(
        thread_id: str,
        body: WikiConversationArchiveRequest,
    ) -> dict:
        """Archive or restore one Wiki conversation."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            archived = set_wiki_conversation_archived(
                state,
                body.archived,
                store,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _conversation_payload(archived)

    @app.get("/api/conversations/{thread_id}/wiki/export")
    def api_export_wiki_conversation(thread_id: str) -> Response:
        """Download one Wiki conversation and its canonical Markdown as ZIP."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            content, filename = export_wiki_conversation(state)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="Wiki thread vault not found") from exc
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.delete("/api/conversations/{thread_id}/wiki")
    def api_delete_wiki_conversation(thread_id: str) -> dict:
        """Permanently delete one Wiki conversation and its thread vault."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            delete_wiki_conversation(state, store)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"deleted": True, "thread_id": thread_id}

    @app.get("/api/conversations/{thread_id}/wiki/commit")
    def api_wiki_commit_status(thread_id: str) -> dict:
        """Return the current Wiki updater and deferred commit state."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_commit_status(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get("/api/conversations/{thread_id}/wiki/diagnostics")
    def api_wiki_diagnostics(thread_id: str) -> dict:
        """Return vault integrity diagnostics for the conversation's Wiki scope."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        return {
            "diagnostics": [
                diagnostic.model_dump(mode="json")
                for diagnostic in get_wiki_diagnostics(state)
            ]
        }

    @app.get("/api/conversations/{thread_id}/wiki/documents")
    def api_wiki_documents(thread_id: str) -> dict:
        """Return the Explorer document summary list for the conversation's Wiki scope."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        return {
            "documents": [
                summary.model_dump(mode="json")
                for summary in get_wiki_document_list(state)
            ]
        }

    @app.get("/api/conversations/{thread_id}/wiki/migration")
    def api_wiki_thread_migration(thread_id: str) -> dict:
        """Return a write-free runtime state-contract migration preview."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_thread_migration(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get("/api/conversations/{thread_id}/wiki/manual-audit")
    def api_wiki_manual_audit(thread_id: str) -> dict:
        """Preview external canonical Markdown changes without writing an archive."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_manual_audit(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/conversations/{thread_id}/wiki/manual-audit/record")
    def api_record_wiki_manual_audit(thread_id: str) -> dict:
        """Record external canonical Markdown changes as an applied manual commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return record_wiki_manual_audit(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/conversations/{thread_id}/wiki/migration/apply")
    def api_apply_wiki_thread_migration(thread_id: str) -> dict:
        """Apply the current runtime state contract as an audited manual commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return apply_wiki_thread_migration(state, store).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/conversations/{thread_id}/wiki/commit/apply")
    def api_apply_wiki_commit(thread_id: str) -> dict:
        """Apply the current Wiki commit.md before the next user message."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return apply_wiki_commit_now(state, store).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/conversations/{thread_id}/wiki/commit/retry")
    async def api_retry_wiki_update(thread_id: str) -> dict:
        """Retry the Wiki updater using the latest accepted turn pair."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = await retry_wiki_update(state, store)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post("/api/conversations/{thread_id}/wiki/commit/regenerate")
    async def api_regenerate_wiki_update(thread_id: str) -> dict:
        """Create a fresh Wiki commit from the latest accepted turn pair."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = await regenerate_wiki_update(state, store)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post("/api/conversations/{thread_id}/wiki/commit/skip")
    def api_skip_wiki_commit(
        thread_id: str,
        body: WikiCommitSkipRequest | None = Body(default=None),
    ) -> dict:
        """Archive the current Wiki commit without applying its patches."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = skip_wiki_commit(state, store, body.reason if body else "")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.get(
        "/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse"
    )
    def api_plan_wiki_commit_inverse(thread_id: str, commit_id: str) -> dict:
        """Return a write-free inverse plan for one applied Wiki commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = plan_wiki_commit_inverse(state, commit_id)
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post(
        "/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse/apply"
    )
    def api_apply_wiki_commit_inverse(thread_id: str, commit_id: str) -> dict:
        """Apply one conflict-free inverse as a new audited Wiki commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = apply_wiki_commit_inverse(state, store, commit_id)
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.post(
        "/api/conversations/{thread_id}/wiki/branch/{message_id}"
    )
    def api_branch_wiki_conversation(
        thread_id: str,
        message_id: str,
    ) -> dict:
        """Create a new Wiki conversation immediately before the selected turn."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = branch_wiki_conversation_before_message(
                state,
                message_id,
                store,
            )
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {
            **_conversation_payload(result.conversation),
            "draft": result.draft,
            "source_thread_id": result.source_thread_id,
            "source_user_message_id": result.source_user_message_id,
        }

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
            if state.world_mode == "wiki":
                result = await reroll_wiki_assistant(
                    state,
                    assistant_id,
                    store,
                    actor_model=body.actor_model if body else None,
                )
            else:
                result = await reroll_assistant(
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

    @app.post("/api/conversations/{thread_id}/tools/{tool_name}")
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

    @app.get("/api/conversations/{thread_id}/schema")
    async def api_conversation_schema(thread_id: str) -> dict:
        """Return schema from the graph viewer server when possible."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
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
        _require_graph_mode(state)
        async with ActiveConversation(state):
            return await fetch_location_board()

    @app.patch("/api/conversations/{thread_id}/locations/move")
    async def api_move_character_location(thread_id: str, body: LocationMoveRequest) -> dict:
        """Move a character to another location in the active conversation graph."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        async with ActiveConversation(state):
            try:
                return await move_character_location(body.character_id, body.location_id)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc

    @app.post("/api/conversations/{thread_id}/pregnancy/force")
    async def api_force_pregnancy(thread_id: str, body: ForcePregnancyRequest) -> dict:
        """Force the mother pregnant by the optional father (확률 무시, 강제 임신)."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        try:
            return await force_pregnancy(state, body.mother_id, body.father_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail="character not found") from exc

    @app.post("/api/conversations/{thread_id}/pregnancy/simulate")
    async def api_simulate_pregnancy(thread_id: str, body: SimulatePregnancyRequest) -> dict:
        """Simulate N internal ejaculations on the mother and apply conception if rolled."""
        state = _load_or_404(store, thread_id)
        _require_graph_mode(state)
        try:
            return await simulate_pregnancy(state, body.mother_id, body.father_id, body.shots, store)
        except KeyError as exc:
            raise HTTPException(404, detail="character not found") from exc

    @app.patch("/api/conversations/{thread_id}/ooc-config")
    def api_update_ooc_config(thread_id: str, body: OocConfigRequest) -> dict:
        """Update the thread-level OOC config."""
        state = _load_or_404(store, thread_id)
        state.ooc_config = body.ooc_config
        store.save(state)
        return {"ooc_config": state.ooc_config}

    @app.get("/api/settings")
    def api_get_settings() -> dict:
        """Return app-wide settings (shared across all conversations)."""
        return load_settings().model_dump()

    @app.patch("/api/settings")
    def api_update_settings(body: AppSettingsRequest) -> dict:
        """Update app-wide settings; only provided fields are changed."""
        settings = load_settings()
        if body.output_repair_enabled is not None:
            settings.output_repair_enabled = body.output_repair_enabled
        save_settings(settings)
        return settings.model_dump()

    @app.get("/api/conversations/{thread_id}/usernotes")
    def api_list_usernotes(thread_id: str) -> dict:
        """Return usernotes shared by this thread's mode and world."""
        state = _load_or_404(store, thread_id)
        return {"usernotes": state.usernotes}

    @app.post("/api/conversations/{thread_id}/usernotes")
    def api_create_usernote(thread_id: str, body: UserNoteCreateRequest) -> dict:
        """Create a world-shared usernote."""
        state = _load_or_404(store, thread_id)
        note = {"id": uuid4().hex, "name": body.name, "content": body.content, "enabled": True}
        state.usernotes = store.add_world_usernote(state, note)
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @app.patch("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_update_usernote(thread_id: str, note_id: str, body: UserNoteUpdateRequest) -> dict:
        """Update an existing world-shared usernote."""
        state = _load_or_404(store, thread_id)
        changes = body.model_dump(exclude_none=True)
        note, state.usernotes = store.update_world_usernote(state, note_id, changes)
        if note is None:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"note": note, "usernotes": state.usernotes}

    @app.delete("/api/conversations/{thread_id}/usernotes/{note_id}")
    def api_delete_usernote(thread_id: str, note_id: str) -> dict:
        """Delete a world-shared usernote."""
        state = _load_or_404(store, thread_id)
        deleted, state.usernotes = store.delete_world_usernote(state, note_id)
        if not deleted:
            raise HTTPException(404, detail="usernote not found")
        store.save(state)
        return {"usernotes": state.usernotes}

    @app.patch("/api/conversations/{thread_id}/messages/{message_id}/variants/activate")
    async def api_activate_variant(thread_id: str, message_id: str, body: VariantActivateRequest) -> dict:
        """Activate a specific version of an assistant message."""
        state = _load_or_404(store, thread_id)
        try:
            if state.world_mode == "wiki":
                return await activate_wiki_variant(
                    state,
                    message_id,
                    body.version_index,
                    store,
                )
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
            if state.world_mode == "wiki":
                return await edit_wiki_message(
                    state,
                    message_id,
                    body.content,
                    store,
                    actor_model=body.actor_model,
                )
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
            if state.world_mode == "wiki":
                return delete_wiki_message(state, message_id, store)
            return delete_message(state, message_id, store)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    app.mount("/", StaticFiles(directory=_APP_DIR), name="static")
    return app


def _load_or_404(store: ConversationStore, thread_id: str) -> ConversationState:
    """Load a conversation or raise HTTP 404."""
    try:
        return store.load(thread_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail="conversation not found") from exc


def _require_graph_mode(state: ConversationState) -> None:
    """Reject graph-only operations for incompatible Wiki conversations."""
    if state.world_mode != "graph":
        raise HTTPException(409, detail="이 기능은 Graph 모드 월드에서만 사용할 수 있습니다.")


def _require_wiki_mode(state: ConversationState) -> None:
    """Reject Wiki commit controls for incompatible Graph conversations."""
    if state.world_mode != "wiki":
        raise HTTPException(409, detail="이 기능은 Wiki 모드 월드에서만 사용할 수 있습니다.")



def _conversation_summary(state) -> dict:
    """Return compact conversation list metadata."""
    return {
        "thread_id": state.thread_id,
        "world_mode": state.world_mode,
        "world_id": state.world_id,
        "scenario_id": state.scenario_id,
        "title": state.title,
        "preview": state.preview,
        "updated_at": state.updated_at.isoformat(),
        "actor_model": state.actor_model,
        "archived": state.archived,
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
