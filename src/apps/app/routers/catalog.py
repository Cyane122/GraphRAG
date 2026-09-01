# ================================
# src/apps/app/routers/catalog.py
#
# General catalog, frontend, and local-console web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register general catalog routes
# ================================

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from ipaddress import ip_address
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from src.apps.app.live_console import get_live_console
from src.apps.app.models import WorldMode, actor_model_catalog
from src.apps.app.routers.shared import RouterContext, _APP_DIR, _json_line
from src.apps.app.runtime import discover_world_profiles, resolve_opening_scene

def create_router(context: RouterContext) -> APIRouter:
    """Register general catalog routes using the shared application context."""
    del context
    router = APIRouter()

    @router.get("/")
    def index() -> FileResponse:
        """Serve the standalone frontend entrypoint."""
        return FileResponse(_APP_DIR / "index.html")

    @router.get("/api/worlds")
    def api_worlds(world_mode: WorldMode = Query(default="graph", alias="mode")) -> dict:
        """Return selectable world/scenario profiles."""
        return {"mode": world_mode, "worlds": discover_world_profiles(world_mode)}

    @router.get("/api/models")
    def api_models() -> dict[str, str | list[dict[str, str]]]:
        """Return the hosted UI Actor model catalog."""
        return actor_model_catalog()

    @router.get("/api/console/stream")
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

    @router.get("/api/opening-scene")
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

    return router
