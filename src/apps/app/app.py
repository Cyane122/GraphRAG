# ================================
# src/apps/app/app.py
#
# FastAPI application factory for the standalone GraphRAG web UI.
#
# Functions
#   - create_app() -> FastAPI : Create the standalone web UI FastAPI app
# ================================

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.apps.app.live_console import configure_live_console
from src.apps.app.routers import catalog, conversations, graph, messages, settings, usernotes, wiki
from src.apps.app.routers.shared import RouterContext, _APP_DIR
from src.apps.app.storage import ConversationStore
from src.config import HOSTED_UI_ORIGINS

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
    context = RouterContext(store=ConversationStore())
    for router in (
        catalog.create_router(context),
        conversations.create_router(context),
        wiki.create_router(context),
        messages.create_router(context),
        graph.create_router(context),
        settings.create_router(context),
        usernotes.create_router(context),
    ):
        app.include_router(router)
    app.mount("/", StaticFiles(directory=_APP_DIR), name="static")
    return app


app = create_app()
