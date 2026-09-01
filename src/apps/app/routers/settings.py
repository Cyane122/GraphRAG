# ================================
# src/apps/app/routers/settings.py
#
# Application and conversation OOC settings web routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register settings routes
# ================================

from __future__ import annotations

from fastapi import APIRouter

from src.apps.app.models import AppSettingsRequest, OocConfigRequest
from src.apps.app.routers.shared import RouterContext, _load_or_404
from src.apps.app.settings import load_settings, normalize_thinking_level, save_settings


def create_router(context: RouterContext) -> APIRouter:
    """Register application and per-conversation settings routes."""
    router = APIRouter()
    store = context.store

    @router.patch("/api/conversations/{thread_id}/ooc-config")
    def api_update_ooc_config(thread_id: str, body: OocConfigRequest) -> dict:
        """Update the thread-level OOC config."""
        state = _load_or_404(store, thread_id)
        state.ooc_config = body.ooc_config
        store.save(state)
        return {"ooc_config": state.ooc_config}

    @router.get("/api/settings")
    def api_get_settings() -> dict:
        """Return app-wide settings (shared across all conversations)."""
        return load_settings().model_dump()

    @router.patch("/api/settings")
    def api_update_settings(body: AppSettingsRequest) -> dict:
        """Update app-wide settings; only provided fields are changed."""
        settings = load_settings()
        if body.output_repair_enabled is not None:
            settings.output_repair_enabled = body.output_repair_enabled
        if body.actor_thinking_level is not None:
            settings.actor_thinking_level = normalize_thinking_level(
                body.actor_thinking_level,
                settings.actor_thinking_level,
            )
        if body.wiki_updater_thinking_level is not None:
            settings.wiki_updater_thinking_level = normalize_thinking_level(
                body.wiki_updater_thinking_level,
                settings.wiki_updater_thinking_level,
            )
        save_settings(settings)
        return settings.model_dump()

    return router
