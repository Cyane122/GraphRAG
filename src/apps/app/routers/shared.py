# ================================
# src/apps/app/routers/shared.py
#
# Shared request helpers and dependencies for web UI routers.
#
# Classes
#   - RouterContext : Shared conversation storage for all route factories
#
# Functions
#   - _load_or_404(store: ConversationStore, thread_id: str) -> ConversationState : Load a conversation or raise HTTP 404
#   - _require_graph_mode(state: ConversationState) -> None : Reject graph-only tools for Wiki threads
#   - _require_wiki_mode(state: ConversationState) -> None : Reject Wiki controls for Graph threads
#   - _conversation_summary(state: ConversationState) -> dict[str, object] : Return compact conversation metadata
#   - _conversation_payload(state: ConversationState) -> dict[str, object] : Return the full conversation payload
#   - _json_line(payload: dict) -> bytes : Encode one newline-delimited JSON event
# ================================

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from src.apps.app.models import ConversationState, _message_payload
from src.apps.app.storage import ConversationStore


_APP_DIR = Path(__file__).resolve().parents[4] / "frontend" / "app"


@dataclass(frozen=True)
class RouterContext:
    """Hold the application-scoped dependencies used by route factories."""

    store: ConversationStore


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


def _conversation_summary(state: ConversationState) -> dict[str, object]:
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


def _conversation_payload(state: ConversationState) -> dict[str, object]:
    """Return full conversation payload for the frontend."""
    return {
        **_conversation_summary(state),
        "ooc_config": state.ooc_config,
        "usernotes": getattr(state, "usernotes", []),
        "messages": [_message_payload(message) for message in state.messages],
    }


def _json_line(payload: dict) -> bytes:
    """Encode one newline-delimited JSON event."""
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
