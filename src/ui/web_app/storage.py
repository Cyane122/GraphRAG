# ================================
# src/ui/web_app/storage.py
#
# JSON persistence for standalone web UI conversations.
#
# Classes
#   - ConversationStore : Load, save, list, and delete standalone conversation state.
#
# Functions
#   - _parse_datetime(value: object) -> datetime : Parse a stored timestamp.
#   - _strip_ui_markers(value: str) -> str : Remove invisible Chainlit UI markers.
#   - _preview(value: str) -> str : Build a compact preview string.
# ================================

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.config import WORLD_ID
from src.ui.web_app.models import ChatMessage, ConversationState

_INDEX_FILE = Path("data") / "index.json"
_UI_MARKERS = (
    "\u2060",
    "\u2061",
    "\u2062",
    "\u2063",
)


def _parse_datetime(value: object) -> datetime:
    """Parse a stored timestamp with a local fallback."""
    if isinstance(value, datetime):
        return value
    text = str(value or "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return datetime.now()


def _strip_ui_markers(value: str) -> str:
    """Remove invisible Chainlit UI marker characters."""
    text = str(value or "")
    for marker in _UI_MARKERS:
        text = text.replace(marker, "")
    return text.strip()


def _preview(value: str) -> str:
    """Build a compact preview string."""
    text = " ".join(_strip_ui_markers(value).split())
    return text[:25] + "..." if len(text) > 26 else text or "새 대화"


class ConversationStore:
    """JSON-backed standalone conversation store."""

    def __init__(self, root: Path | str = Path("data") / "threads") -> None:
        """Create a store rooted at the given directory."""
        self.root = Path(root)

    def _path(self, thread_id: str) -> Path:
        """Return the JSON path for a thread id."""
        return self.root / f"{thread_id}.json"

    def _legacy_path(self, thread_id: str) -> Path:
        """Return the legacy Chainlit chat.json path for a thread id."""
        return self.root / thread_id / "chat.json"

    def save(self, state: ConversationState) -> ConversationState:
        """Persist and return a conversation state."""
        state.updated_at = datetime.now()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(state.thread_id).write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return state

    def load(self, thread_id: str) -> ConversationState:
        """Load a conversation state or raise FileNotFoundError."""
        path = self._path(thread_id)
        if not path.exists():
            return self._load_legacy(thread_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ConversationState.model_validate(payload)

    def exists(self, thread_id: str) -> bool:
        """Return whether a conversation file exists."""
        return self._path(thread_id).exists()

    def list(self) -> list[ConversationState]:
        """Return all conversations ordered by latest update first."""
        if not self.root.exists():
            return []
        states_by_id: dict[str, ConversationState] = {}
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if "thread_id" in payload and "messages" in payload:
                    state = ConversationState.model_validate(payload)
                    states_by_id[state.thread_id] = state
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        for thread_id in self._legacy_thread_ids():
            if thread_id in states_by_id:
                continue
            try:
                states_by_id[thread_id] = self._load_legacy(thread_id)
            except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError):
                continue
        return sorted(states_by_id.values(), key=lambda item: item.updated_at, reverse=True)

    def _legacy_thread_ids(self) -> list[str]:
        """Return legacy thread ids from index and existing chat folders."""
        ids: list[str] = []
        if _INDEX_FILE.exists():
            try:
                index = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
                ids.extend(str(item["id"]) for item in index.get("threads", []) if item.get("id"))
            except (OSError, json.JSONDecodeError):
                pass
        ids.extend(path.parent.name for path in self.root.glob("*/chat.json"))
        seen: set[str] = set()
        result: list[str] = []
        for thread_id in ids:
            if thread_id not in seen:
                seen.add(thread_id)
                result.append(thread_id)
        return result

    def _load_legacy(self, thread_id: str) -> ConversationState:
        """Load a legacy Chainlit chat.json thread as ConversationState."""
        payload = json.loads(self._legacy_path(thread_id).read_text(encoding="utf-8"))
        world_id, scenario_id = self._legacy_world(payload)
        messages = self._legacy_messages(payload)
        assistants = [message.content for message in messages if message.role == "assistant"]
        title = _strip_ui_markers(str(payload.get("name") or "새 채팅"))
        preview_source = assistants[-1] if assistants else title
        return ConversationState(
            thread_id=thread_id,
            world_id=world_id,
            scenario_id=scenario_id,
            title=title or f"{world_id}/{scenario_id}",
            preview=_preview(preview_source),
            created_at=_parse_datetime(payload.get("createdAt")),
            updated_at=_parse_datetime(payload.get("updatedAt") or payload.get("createdAt")),
            messages=messages,
            history=[
                {"role": message.role, "content": message.content, "msg_id": message.id}
                for message in messages
            ],
            recent_responses=[content[:1500] for content in assistants[-3:]],
        )

    def _legacy_world(self, payload: dict) -> tuple[str, str]:
        """Recover world/scenario ids from legacy metadata or tags."""
        metadata = payload.get("metadata") or {}
        tags = payload.get("tags") or []
        profile = metadata.get("chat_profile")
        if not profile and tags:
            profile = next((tag for tag in tags if isinstance(tag, str) and tag), None)
        if profile:
            world_id, _, scenario_id = str(profile).partition("/")
            return world_id or WORLD_ID, scenario_id or "default"
        return str(metadata.get("world_id") or WORLD_ID), str(metadata.get("scenario_id") or "default")

    def _legacy_messages(self, payload: dict) -> list[ChatMessage]:
        """Convert Chainlit steps into frontend chat messages."""
        messages: list[ChatMessage] = []
        latest_user_id: str | None = None
        for step in payload.get("steps") or []:
            step_type = step.get("type")
            if step_type not in {"user_message", "assistant_message"}:
                continue
            content = _strip_ui_markers(str(step.get("output") or ""))
            if not content:
                continue
            role = "user" if step_type == "user_message" else "assistant"
            parent_user_id = latest_user_id if role == "assistant" else None
            message = ChatMessage(
                id=str(step.get("id") or ""),
                role=role,
                content=content,
                created_at=_parse_datetime(step.get("createdAt") or step.get("start")),
                parent_user_id=parent_user_id,
            )
            messages.append(message)
            if role == "user":
                latest_user_id = message.id
        return messages
