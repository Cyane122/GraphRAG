# ================================
# src/apps/app/storage.py
#
# JSON persistence for standalone web UI conversations.
#
# Classes
#   - ConversationStore : Persist conversations and a global usernote library shared by
#     every world and mode, with each note's enabled state stored per conversation thread.
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
        self._usernotes_lock = Lock()

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
        toggle or thread-level OOC config or Wiki system override the user edited while the
        response was streaming would be clobbered by the stale snapshot. The generation path
        never writes these fields, so re-reading the current on-disk values just before that
        save prevents the lost update.
        """
        path = self._path(state.thread_id)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                if isinstance(payload.get("enabled_usernote_ids"), list):
                    state.enabled_usernote_ids = [
                        str(note_id) for note_id in payload["enabled_usernote_ids"]
                    ]
                if isinstance(payload.get("ooc_config"), str):
                    state.ooc_config = payload["ooc_config"]
                state.wiki_system_overrides = normalize_wiki_system_overrides(
                    payload.get("wiki_system_overrides")
                )
        state.usernotes = self.load_usernotes(state)

    def load(self, thread_id: str) -> ConversationState:
        """Load a conversation state or raise FileNotFoundError."""
        path = self._path(thread_id)
        if not path.exists():
            state = self._load_legacy(thread_id)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            state = ConversationState.model_validate(payload)
        state.usernotes = self.load_usernotes(state)
        return sync_conversation_perspective(state)

    def load_usernotes(self, state: ConversationState) -> list[dict[str, Any]]:
        """Load the global usernote library, hydrated with this thread's enabled state.

        Migrates the global library from legacy per-world files and legacy thread-embedded
        notes on first access, and derives this thread's `enabled_usernote_ids` from its
        legacy sources the first time it is loaded after the migration.
        """
        with self._usernotes_lock:
            library = self._ensure_library_unlocked()
            self._ensure_enabled_ids_unlocked(state, library)
            return self._hydrate_usernotes(library, state.enabled_usernote_ids or [])

    def add_usernote(
        self,
        state: ConversationState,
        note: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Add one usernote to the global library and enable it for this thread only."""
        with self._usernotes_lock:
            library = self._ensure_library_unlocked()
            self._ensure_enabled_ids_unlocked(state, library)
            note_id = str(note["id"])
            library.append(
                {"id": note_id, "name": note.get("name", ""), "content": note.get("content", "")}
            )
            self._write_library_unlocked(library)
            ids = list(state.enabled_usernote_ids or [])
            if note.get("enabled", True):
                if note_id not in ids:
                    ids.append(note_id)
            elif note_id in ids:
                ids.remove(note_id)
            state.enabled_usernote_ids = ids
            return self._hydrate_usernotes(library, ids)

    def update_usernote(
        self,
        state: ConversationState,
        note_id: str,
        changes: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Update one usernote's library fields and/or this thread's enabled state.

        `name`/`content` in `changes` are written to the shared global library; `enabled`
        only changes this thread's `enabled_usernote_ids`. Returns the hydrated note (as
        seen by this thread) with the full hydrated list, or `(None, list)` if the note id
        does not exist in the library.
        """
        with self._usernotes_lock:
            library = self._ensure_library_unlocked()
            self._ensure_enabled_ids_unlocked(state, library)
            entry = next((item for item in library if item.get("id") == note_id), None)
            if entry is None:
                return None, self._hydrate_usernotes(library, state.enabled_usernote_ids or [])
            library_changes = {key: changes[key] for key in ("name", "content") if key in changes}
            if library_changes:
                entry.update(library_changes)
                self._write_library_unlocked(library)
            ids = list(state.enabled_usernote_ids or [])
            if "enabled" in changes:
                if changes["enabled"]:
                    if note_id not in ids:
                        ids.append(note_id)
                elif note_id in ids:
                    ids.remove(note_id)
                state.enabled_usernote_ids = ids
            hydrated = self._hydrate_usernotes(library, state.enabled_usernote_ids or [])
            note = next((item for item in hydrated if item.get("id") == note_id), None)
            return note, hydrated

    def delete_usernote(
        self,
        state: ConversationState,
        note_id: str,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Delete one usernote from the global library and from this thread's enabled ids."""
        with self._usernotes_lock:
            library = self._ensure_library_unlocked()
            self._ensure_enabled_ids_unlocked(state, library)
            remaining = [item for item in library if item.get("id") != note_id]
            deleted = len(remaining) != len(library)
            if deleted:
                self._write_library_unlocked(remaining)
            ids = [item_id for item_id in (state.enabled_usernote_ids or []) if item_id != note_id]
            state.enabled_usernote_ids = ids
            return deleted, self._hydrate_usernotes(remaining, ids)

    def _usernotes_library_path(self) -> Path:
        """Return the single global usernote library file shared by every world and mode."""
        return self.root.parent / "usernotes.json"

    def _ensure_library_unlocked(self) -> list[dict[str, Any]]:
        """Load the global library, migrating legacy sources into it on first access."""
        path = self._usernotes_library_path()
        if path.exists():
            return self._read_library_unlocked(path)
        library = self._migrate_library_unlocked()
        self._write_library_unlocked(library)
        return library

    def _read_library_unlocked(self, path: Path) -> list[dict[str, Any]]:
        """Read the global usernote library file while the caller holds the store lock."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        notes = payload.get("usernotes", []) if isinstance(payload, dict) else []
        return [
            {"id": str(note["id"]), "name": note.get("name", ""), "content": note.get("content", "")}
            for note in notes
            if isinstance(note, dict) and note.get("id")
        ]

    def _migrate_library_unlocked(self) -> list[dict[str, Any]]:
        """Merge legacy per-world usernote files and legacy thread-embedded notes.

        Notes are deduplicated by id (first occurrence wins): every legacy
        `<world_root>/<mode>/<world_id>/usernotes.json` file, sorted by path, then the
        legacy `usernotes` arrays embedded in thread JSON files under `self.root`. The
        legacy `enabled` flag is dropped, since enabled state now lives per thread.
        """
        notes_by_id: dict[str, dict[str, Any]] = {}
        if self.world_root.exists():
            for path in sorted(self.world_root.glob("*/*/usernotes.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                notes = payload.get("usernotes", []) if isinstance(payload, dict) else []
                for note in notes:
                    if isinstance(note, dict) and note.get("id"):
                        note_id = str(note["id"])
                        notes_by_id.setdefault(
                            note_id,
                            {"id": note_id, "name": note.get("name", ""), "content": note.get("content", "")},
                        )
        if self.root.exists():
            for path in sorted(self.root.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for note in payload.get("usernotes") or []:
                    if isinstance(note, dict) and note.get("id"):
                        note_id = str(note["id"])
                        notes_by_id.setdefault(
                            note_id,
                            {"id": note_id, "name": note.get("name", ""), "content": note.get("content", "")},
                        )
        return list(notes_by_id.values())

    def _write_library_unlocked(self, notes: list[dict[str, Any]]) -> None:
        """Atomically persist the global usernote library while holding the lock."""
        path = self._usernotes_library_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"usernotes": notes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _legacy_scoped_usernotes_path(self, state: ConversationState) -> Path:
        """Return the legacy mode-scoped world usernote file used only as a migration source."""
        return (
            self.world_root
            / _safe_scope_part(state.world_mode)
            / _safe_scope_part(state.world_id)
            / "usernotes.json"
        )

    def _ensure_enabled_ids_unlocked(
        self,
        state: ConversationState,
        library: list[dict[str, Any]],
    ) -> None:
        """Derive `state.enabled_usernote_ids` once from this thread's legacy enabled state.

        When the legacy mode-scoped world usernote file for this thread's world exists, its
        `enabled` notes seed this thread's enabled ids (preserving what this thread's prompt
        already showed). Otherwise the thread's own legacy `usernotes` entries are used. Ids
        that no longer exist in the global library are dropped. Does nothing if this state
        was already migrated (`enabled_usernote_ids` is not None).
        """
        if state.enabled_usernote_ids is not None:
            return
        library_ids = {note["id"] for note in library}
        legacy_path = self._legacy_scoped_usernotes_path(state)
        ids: list[str] = []
        if legacy_path.exists():
            try:
                payload = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            notes = payload.get("usernotes", []) if isinstance(payload, dict) else []
            for note in notes:
                if isinstance(note, dict) and note.get("enabled") and note.get("id"):
                    ids.append(str(note["id"]))
        else:
            for note in state.usernotes:
                if isinstance(note, dict) and note.get("enabled") and note.get("id"):
                    ids.append(str(note["id"]))
        state.enabled_usernote_ids = [note_id for note_id in ids if note_id in library_ids]

    def _hydrate_usernotes(
        self,
        library: list[dict[str, Any]],
        enabled_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Build this thread's usernote view: every library note tagged with enabled state."""
        enabled_set = set(enabled_ids)
        return [{**note, "enabled": note["id"] in enabled_set} for note in library]

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
