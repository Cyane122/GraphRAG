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
#   - WikiCommitSkipRequest : Request body for discarding a pending Wiki commit.
#   - WikiCommitStatusResponse : Current Wiki updater and deferred commit state.
#   - WikiBranchResult : 안전한 과거 턴 분기로 만든 대화와 재입력 초안.
#   - WikiConversationRenameRequest : Wiki 대화 이름 변경 요청.
#   - WikiConversationArchiveRequest : Wiki 대화 보관 또는 복원 요청.
#   - WikiSystemsPatchRequest : 대화별 Wiki postprocessor override 변경 요청.
#   - WikiSystemsResponse : 대화별 Wiki postprocessor 유효값·기본값·override 상태 응답.
#   - AppSettingsRequest : Request body for updating app-wide settings.
#   - ForcePregnancyRequest : Request body for forcing a pregnancy (mother + optional father).
#   - SimulatePregnancyRequest : Request body for simulating N internal ejaculations.
#
# Functions
#   - normalize_actor_model(model_name: str | None) -> str : Return a supported Actor model id.
#   - normalize_wiki_system_overrides(overrides: Mapping[str, object] | None) -> dict[str, bool] : 저장용 Wiki system override를 canonical bool dict로 정리합니다.
#   - resolve_wiki_systems(overrides: Mapping[str, object] | None, defaults: Mapping[str, bool]) -> dict[str, bool] : override와 기본값을 합쳐 대화의 유효 Wiki system 표를 만듭니다.
#   - overridden_wiki_system_names(overrides: Mapping[str, object] | None) -> list[str] : 명시적으로 설정된 Wiki system 키를 canonical 순서로 반환합니다.
#   - apply_wiki_system_patch(overrides: Mapping[str, object] | None, patch: Mapping[str, bool | None]) -> dict[str, bool] : PATCH payload를 기존 override dict에 적용합니다.
# ================================

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field

from src.config import WIKI_SYSTEM_KEYS

WorldMode = Literal["graph", "wiki"]
WikiUpdateStatus = Literal["idle", "queued", "failed", "applied", "skipped"]

SUPPORTED_ACTOR_MODELS = {
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-8": "Claude Opus 4.8",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
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


def normalize_wiki_system_overrides(
    overrides: Mapping[str, object] | None,
) -> dict[str, bool]:
    """저장용 Wiki system override를 canonical bool dict로 정리합니다."""
    normalized: dict[str, bool] = {}
    if overrides is None:
        return normalized
    for key in WIKI_SYSTEM_KEYS:
        value = overrides.get(key)
        if isinstance(value, bool):
            normalized[key] = value
    return normalized


def resolve_wiki_systems(
    overrides: Mapping[str, object] | None,
    defaults: Mapping[str, bool],
) -> dict[str, bool]:
    """override와 기본값을 합쳐 대화의 유효 Wiki system 표를 만듭니다."""
    resolved = {key: bool(defaults[key]) for key in WIKI_SYSTEM_KEYS}
    resolved.update(normalize_wiki_system_overrides(overrides))
    return resolved


def overridden_wiki_system_names(overrides: Mapping[str, object] | None) -> list[str]:
    """명시적으로 설정된 Wiki system 키를 canonical 순서로 반환합니다."""
    normalized = normalize_wiki_system_overrides(overrides)
    return [key for key in WIKI_SYSTEM_KEYS if key in normalized]


def apply_wiki_system_patch(
    overrides: Mapping[str, object] | None,
    patch: Mapping[str, bool | None],
) -> dict[str, bool]:
    """PATCH payload를 기존 override dict에 적용합니다."""
    unknown = sorted(key for key in patch if key not in WIKI_SYSTEM_KEYS)
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"Unknown wiki systems: {names}")
    updated = normalize_wiki_system_overrides(overrides)
    for key in WIKI_SYSTEM_KEYS:
        if key not in patch:
            continue
        value = patch[key]
        if value is None:
            updated.pop(key, None)
            continue
        updated[key] = value
    return updated


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
    wiki_commit_id: str | None = None


class ConversationState(BaseModel):
    """Persisted standalone UI conversation state."""

    thread_id: str = Field(default_factory=lambda: uuid4().hex)
    world_mode: WorldMode = "graph"
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
    archived: bool = False
    wiki_update_status: WikiUpdateStatus = "idle"
    wiki_update_error: str = ""
    wiki_pending_commit_id: str | None = None
    wiki_system_overrides: dict[str, bool] = Field(default_factory=dict)


class WikiBranchResult(BaseModel):
    """A newly reconstructed Wiki branch and the selected user input draft."""

    conversation: ConversationState
    draft: str
    source_thread_id: str
    source_user_message_id: str


class WikiConversationRenameRequest(BaseModel):
    """Request body for changing a Wiki conversation's display title."""

    title: str = Field(min_length=1, max_length=120)


class WikiConversationArchiveRequest(BaseModel):
    """Request body for archiving or restoring a Wiki conversation."""

    archived: bool


class WikiSystemsPatchRequest(BaseModel):
    """Request body for partially updating per-conversation Wiki system overrides."""

    systems: dict[str, bool | None]


class WikiSystemsResponse(BaseModel):
    """Resolved Wiki system state for one conversation."""

    systems: dict[str, bool]
    defaults: dict[str, bool]
    overridden: list[str]
    authored_cycle_characters: list[str]


class ConversationCreateRequest(BaseModel):
    """Request body for creating a standalone conversation."""

    world_id: str
    world_mode: WorldMode = "graph"
    scenario_id: str | None = None
    actor_model: str | None = None
    ooc_config: str = ""


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


class WikiCommitSkipRequest(BaseModel):
    """Request body for discarding a pending Wiki commit without applying it."""

    reason: str = Field(default="", max_length=500)


class WikiCommitStatusResponse(BaseModel):
    """Current Wiki updater state plus the filesystem-authoritative commit payload."""

    update_status: WikiUpdateStatus
    update_error: str = ""
    commit: dict[str, Any] | None = None
    wiki_thread_generation: Literal["current", "legacy", "missing"] = "missing"
    wiki_thread_diagnostic: str = ""


class AppSettingsRequest(BaseModel):
    """Request body for updating app-wide settings (partial update)."""

    output_repair_enabled: bool | None = None
    actor_thinking_level: str | None = None
    wiki_updater_thinking_level: str | None = None


class ForcePregnancyRequest(BaseModel):
    """Request body for forcing a pregnancy (mother conceives by father)."""

    mother_id: str
    father_id: str | None = None


class SimulatePregnancyRequest(BaseModel):
    """Request body for simulating N internal ejaculations and applying conception."""

    mother_id: str
    father_id: str | None = None
    shots: int = Field(default=1, ge=1, le=999)
