# ================================
# src/apps/app/models.py
#
# Standalone web UI request, response, and persistence models.
#
# Classes
#   - MessageVariant : Previous assistant response retained after reroll.
#   - ChatMessage : Persisted frontend message record.
#   - ConversationState : Persisted conversation thread state.
#   - ConversationCreateRequest : Request body for conversation creation.
#   - MessageCreateRequest : Request body for user message generation.
#   - MessageRerollRequest : Request body for assistant reroll generation.
#   - MessageEditRequest : Request body for message editing.
#   - LocationMoveRequest : Request body for moving a character between locations.
#   - OocConfigRequest : Request body for updating thread OOC config.
#   - UserNoteCreateRequest : Request body for creating a usernote.
#   - UserNoteUpdateRequest : Request body for updating a usernote.
#
# Functions
#   - normalize_actor_model(model_name: str | None) -> str : Return a supported Actor model id.
# ================================

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

SUPPORTED_ACTOR_MODELS = {
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-8": "Claude Opus 4.8",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3-flash-preview": "Gemini 3 Flash Preview",
}
DEFAULT_ACTOR_MODEL = "gemini-3.1-pro-preview"
ACTOR_MODEL_ALIASES: dict[str, str] = {}


def normalize_actor_model(model_name: str | None) -> str:
    """Return a supported Actor model id, falling back to the default."""
    candidate = str(model_name or "").strip()
    candidate = ACTOR_MODEL_ALIASES.get(candidate, candidate)
    if candidate in SUPPORTED_ACTOR_MODELS:
        return candidate
    return DEFAULT_ACTOR_MODEL


class MessageVariant(BaseModel):
    """Previous assistant response retained after reroll."""

    id: str = Field(default_factory=lambda: f"variant_{uuid4().hex}")
    content: str
    created_at: datetime
    actor_model: str | None = None
    edited: bool = False


class ChatMessage(BaseModel):
    """Persisted frontend message record."""

    id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
    parent_user_id: str | None = None
    edited: bool = False
    actor_model: str | None = None
    variants: list[MessageVariant] = Field(default_factory=list)
    ooc_config: str = ""


class ConversationState(BaseModel):
    """Persisted standalone UI conversation state."""

    thread_id: str = Field(default_factory=lambda: uuid4().hex)
    world_id: str
    scenario_id: str | None = None
    title: str = "새 대화"
    preview: str = "새 대화"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    messages: list[ChatMessage] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    recent_responses: list[str] = Field(default_factory=list)
    pending_commit: dict[str, Any] | None = None
    prev_cot: str = ""
    scene_need_hints: dict[str, str] = Field(default_factory=dict)
    pending_kakao_messages: list[dict[str, Any]] = Field(default_factory=list)
    pending_ooc: str = ""
    ooc_config: str = ""
    usernotes: list[dict[str, Any]] = Field(default_factory=list)
    narrative_turns: list[dict[str, Any]] = Field(default_factory=list)
    actor_model: str = DEFAULT_ACTOR_MODEL
    world_config: dict[str, Any] = Field(default_factory=dict)
    pc_id: str = ""
    npc_id: str = ""
    npc_name_kor: str = ""
    perspective: int = 3


class ConversationCreateRequest(BaseModel):
    """Request body for creating a standalone conversation."""

    world_id: str
    scenario_id: str | None = None
    actor_model: str | None = None


class MessageCreateRequest(BaseModel):
    """Request body for generating an assistant response."""

    content: str
    client_message_id: str | None = None
    actor_model: str | None = None


class MessageRerollRequest(BaseModel):
    """Request body for rerolling an assistant response."""

    actor_model: str | None = None


class MessageEditRequest(BaseModel):
    """Request body for editing a user or assistant message."""

    content: str
    actor_model: str | None = None


class LocationMoveRequest(BaseModel):
    """Request body for moving a character between locations."""

    character_id: str
    location_id: str


class VariantActivateRequest(BaseModel):
    """Request body for activating a specific message version."""

    version_index: int


class OocConfigRequest(BaseModel):
    """Request body for updating the thread-level OOC config."""

    ooc_config: str


class UserNoteCreateRequest(BaseModel):
    """Request body for creating a new usernote."""

    name: str
    content: str


class UserNoteUpdateRequest(BaseModel):
    """Request body for updating an existing usernote."""

    name: str | None = None
    content: str | None = None
    enabled: bool | None = None
