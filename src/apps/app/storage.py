# ================================
# src/apps/app/storage.py
#
# JSON persistence for standalone web UI conversations.
#
# Classes
#   - ConversationStore : Persist conversations and mode-scoped, world-shared usernotes.
#
# Functions
#   - _parse_datetime(value: object) -> datetime : Parse a stored timestamp.
#   - _strip_ui_markers(value: str) -> str : Remove invisible UI markers from stored message content.
#   - _preview(value: str) -> str : Build a compact preview string.
#   - _safe_scope_part(value: str) -> str : Normalize a mode or world id for storage paths.
# ================================

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from src.config import WORLD_ID
from src.apps.app.models import (
    ChatMessage,
    ConversationState,
    normalize_wiki_system_overrides,
)
from src.apps.app.runtime import sync_conversation_perspective

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


def _safe_scope_part(value: str) -> str:
    """Normalize a mode or world id for a storage path segment."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return normalized or "default"


class ConversationStore:
    """JSON-backed standalone conversation store."""

    def __init__(self, root: Path | str = Path("data") / "threads") -> None:
        """Create a store rooted at the given directory."""
        self.root = Path(root)
        self.world_root = self.root.parent / "worlds"
        self._world_usernotes_lock = Lock()

    def _path(self, thread_id: str) -> Path:
        """Return the JSON path for a thread id."""
        return self.root / f"{thread_id}.json"

    def _legacy_path(self, thread_id: str) -> Path:
        """Return the legacy Chainlit chat.json path for a thread id."""
        return self.root / thread_id / "chat.json"

    def save(self, state: ConversationState) -> ConversationState:
        """Persist and return a conversation state."""
        sync_conversation_perspective(state)
        state.wiki_system_overrides = normalize_wiki_system_overrides(
            state.wiki_system_overrides
        )
        state.updated_at = datetime.now()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(state.thread_id).write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return state

    def delete(self, thread_id: str) -> None:
        """Delete exactly one modern conversation JSON record."""
        root = self.root.resolve()
        path = self._path(thread_id).resolve()
        if path.parent != root:
            raise ValueError("Conversation path escapes the storage root")
        if not path.is_file():
            raise FileNotFoundError(thread_id)
        path.unlink()

    def refresh_out_of_band_fields(self, state: ConversationState) -> None:
        """Reload fields that are edited through independent endpoints into `state`.

        A streaming generation loads a snapshot at turn start and re-persists the whole
        state in its `finally` block. That save can run many seconds later, so any usernote
        or thread-level OOC config or Wiki system override the user edited while the response
        was streaming would be clobbered by the stale snapshot. The generation path never
        writes these fields, so re-reading the current on-disk values just before that save
        prevents the lost update.
        """
        state.usernotes = self.load_world_usernotes(state)
        path = self._path(state.thread_id)
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload.get("ooc_config"), str):
            state.ooc_config = payload["ooc_config"]
        state.wiki_system_overrides = normalize_wiki_system_overrides(
            payload.get("wiki_system_overrides")
        )

    def load(self, thread_id: str) -> ConversationState:
        """Load a conversation state or raise FileNotFoundError."""
        path = self._path(thread_id)
        if not path.exists():
            state = self._load_legacy(thread_id)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            state = ConversationState.model_validate(payload)
        state.usernotes = self.load_world_usernotes(state)
        return sync_conversation_perspective(state)

    def load_world_usernotes(self, state: ConversationState) -> list[dict[str, Any]]:
        """Load usernotes shared by every thread in the same mode and world."""
        with self._world_usernotes_lock:
            return self._ensure_world_usernotes_unlocked(state)

    def add_world_usernote(
        self,
        state: ConversationState,
        note: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Append one usernote to a mode-scoped world and return the updated list."""
        with self._world_usernotes_lock:
            notes = self._ensure_world_usernotes_unlocked(state)
            notes.append(note)
            self._write_world_usernotes_unlocked(state, notes)
            return notes

    def update_world_usernote(
        self,
        state: ConversationState,
        note_id: str,
        changes: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Update one world-shared usernote and return it with the full list."""
        with self._world_usernotes_lock:
            notes = self._ensure_world_usernotes_unlocked(state)
            note = next((item for item in notes if item.get("id") == note_id), None)
            if note is None:
                return None, notes
            note.update(changes)
            self._write_world_usernotes_unlocked(state, notes)
            return note, notes

    def delete_world_usernote(
        self,
        state: ConversationState,
        note_id: str,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Delete one world-shared usernote and return whether it existed."""
        with self._world_usernotes_lock:
            notes = self._ensure_world_usernotes_unlocked(state)
            remaining = [item for item in notes if item.get("id") != note_id]
            if len(remaining) == len(notes):
                return False, notes
            self._write_world_usernotes_unlocked(state, remaining)
            return True, remaining

    def _world_usernotes_path(self, state: ConversationState) -> Path:
        """Return the shared usernote file for one incompatible world namespace."""
        return (
            self.world_root
            / _safe_scope_part(state.world_mode)
            / _safe_scope_part(state.world_id)
            / "usernotes.json"
        )

    def _ensure_world_usernotes_unlocked(self, state: ConversationState) -> list[dict[str, Any]]:
        """Load shared notes, migrating legacy thread notes on first access."""
        path = self._world_usernotes_path(state)
        if path.exists():
            return self._read_world_usernotes_unlocked(path)
        notes = self._legacy_usernotes_for_world(state)
        self._write_world_usernotes_unlocked(state, notes)
        return notes

    def _read_world_usernotes_unlocked(self, path: Path) -> list[dict[str, Any]]:
        """Read a world usernote file while the caller holds the store lock."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        notes = payload.get("usernotes", []) if isinstance(payload, dict) else []
        return [dict(note) for note in notes if isinstance(note, dict)]

    def _legacy_usernotes_for_world(self, state: ConversationState) -> list[dict[str, Any]]:
        """Collect legacy per-thread notes once when creating a shared world file."""
        notes_by_id: dict[str, dict[str, Any]] = {}
        if self.root.exists():
            for path in sorted(self.root.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                payload_mode = str(payload.get("world_mode") or "graph")
                if payload_mode != state.world_mode or str(payload.get("world_id") or "") != state.world_id:
                    continue
                for note in payload.get("usernotes") or []:
                    if isinstance(note, dict) and note.get("id"):
                        notes_by_id.setdefault(str(note["id"]), dict(note))
        for note in state.usernotes:
            if isinstance(note, dict) and note.get("id"):
                notes_by_id.setdefault(str(note["id"]), dict(note))
        return list(notes_by_id.values())

    def _write_world_usernotes_unlocked(
        self,
        state: ConversationState,
        notes: list[dict[str, Any]],
    ) -> None:
        """Atomically persist mode-scoped world usernotes while holding the lock."""
        path = self._world_usernotes_path(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "world_mode": state.world_mode,
                    "world_id": state.world_id,
                    "usernotes": notes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

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
                    state = sync_conversation_perspective(ConversationState.model_validate(payload))
                    states_by_id[state.thread_id] = state
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        for thread_id in self._legacy_thread_ids():
            if thread_id in states_by_id:
                continue
            try:
                states_by_id[thread_id] = sync_conversation_perspective(self._load_legacy(thread_id))
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
