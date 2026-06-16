# ================================
# src/apps/app/session_models.py
#
# Session data models for per-turn state in the web UI.
#
# Classes
#   - PendingCommit : Deferred side effects waiting for acceptance on the next turn.
# ================================

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class PendingCommit(BaseModel):
    """Deferred actor response data stored in the web UI session."""

    commit_id: str = Field(default_factory=lambda: uuid4().hex)
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    committed_at: datetime | None = None
    failure_reason: str | None = None
    failed_stage: str | None = None
    completed_stages: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    user_input: str
    ai_response: str
    scene_types: list[str]
    scene_chars: list[str]
    timestamp: datetime
    history_snapshot: list[dict[str, Any]]
    recent_snapshot: list[str]
    prev_game_time: str | None
    manager_effects: dict[str, Any]
    ooc_result: dict[str, Any] | None = None
    time_plan: dict[str, Any] | None = None
    pending_kakao_messages: list[dict[str, Any]] = Field(default_factory=list)
    pending_effects: list[dict[str, Any]] = Field(default_factory=list)
    pending_state_diff: list[dict[str, Any]] = Field(default_factory=list)
    committed_diff: list[dict[str, Any]] = Field(default_factory=list)
    rejected_diff: list[dict[str, Any]] = Field(default_factory=list)
    debug_dir: Path | str | None = None
    user_msg_id: str | None = None
    response_msg_id: str | None = None
    prev_cot: str = ""
