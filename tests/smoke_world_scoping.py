# ================================
# tests/smoke_world_scoping.py
#
# 전역 유저노트 라이브러리(월드·모드 공유)와 스레드별 enabled 상태 분리를 검증합니다.
#
# Functions
#   - _state(thread_id: str, world_mode: WorldMode, world_id: str, note_id: str) -> ConversationState : 검증용 대화 상태를 만듭니다.
#   - _write_legacy_world_file(store: ConversationStore, world_mode: WorldMode, world_id: str, notes: list[dict]) -> None : 마이그레이션 대상 레거시 world 파일을 만듭니다.
#   - main() -> None : 전역 라이브러리 마이그레이션, 스레드별 enabled 격리, 편집 공유, 삭제 전파, lost-update 방지, 신규 대화 기본값을 검증합니다.
# ================================

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.models import ConversationState, WorldMode  # noqa: E402
from src.apps.app.storage import ConversationStore, _safe_scope_part  # noqa: E402


def _state(thread_id: str, world_mode: WorldMode, world_id: str, note_id: str) -> ConversationState:
    """Create a validation conversation with one legacy per-thread note."""
    return ConversationState(
        thread_id=thread_id,
        world_mode=world_mode,
        world_id=world_id,
        world_config={"perspective": 3},
        usernotes=[{"id": note_id, "name": note_id, "content": note_id, "enabled": True}],
    )


def _write_legacy_world_file(
    store: ConversationStore,
    world_mode: WorldMode,
    world_id: str,
    notes: list[dict],
) -> None:
    """Write a legacy mode-scoped world usernote file as a migration source."""
    path = (
        store.world_root
        / _safe_scope_part(world_mode)
        / _safe_scope_part(world_id)
        / "usernotes.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"world_mode": world_mode, "world_id": world_id, "usernotes": notes}),
        encoding="utf-8",
    )


def main() -> None:
    """Verify the shared global library and per-thread enabled isolation."""
    with TemporaryDirectory() as temporary:
        threads_root = Path(temporary) / "threads"
        store = ConversationStore(threads_root)

        # --- 1. Legacy migration: world files (graph/w1, wiki/w2) + legacy thread note. ---
        _write_legacy_world_file(
            store,
            "graph",
            "w1",
            [{"id": "graph_note", "name": "graph_note", "content": "graph_note", "enabled": True}],
        )
        _write_legacy_world_file(
            store,
            "wiki",
            "w2",
            [{"id": "wiki_note", "name": "wiki_note", "content": "wiki_note", "enabled": False}],
        )
        thread_a = _state("thread_a", "graph", "w1", "graph_note")
        thread_b = _state("thread_b", "wiki", "w2", "wiki_note")
        legacy_only = _state("thread_c", "graph", "w3", "legacy_embedded")
        store.save(thread_a)
        store.save(thread_b)
        store.save(legacy_only)

        loaded_a = store.load("thread_a")
        library_ids = {note["id"] for note in loaded_a.usernotes}
        assert library_ids == {"graph_note", "wiki_note", "legacy_embedded"}, library_ids
        # thread_a's world file marked graph_note enabled: preserved on migration.
        assert {n["id"] for n in loaded_a.usernotes if n["enabled"]} == {"graph_note"}

        loaded_b = store.load("thread_b")
        # thread_b's world file marked wiki_note disabled: preserved on migration.
        assert {n["id"] for n in loaded_b.usernotes if n["enabled"]} == set()

        loaded_c = store.load("thread_c")
        # thread_c has no legacy world file, so its own embedded note's enabled flag is used.
        assert {n["id"] for n in loaded_c.usernotes if n["enabled"]} == {"legacy_embedded"}

        # --- 2. A note added from a graph thread is visible (disabled) from a wiki thread
        #        in another world. ---
        added = store.add_usernote(
            loaded_a,
            {"id": "cross_world", "name": "cross", "content": "cross", "enabled": True},
        )
        assert "cross_world" in {n["id"] for n in added}
        loaded_a.usernotes = added
        store.save(loaded_a)
        reloaded_b = store.load("thread_b")
        cross_in_b = next(n for n in reloaded_b.usernotes if n["id"] == "cross_world")
        assert cross_in_b["enabled"] is False

        # --- 3. Toggling enabled in thread A does not change thread B. ---
        note, notes_a = store.update_usernote(loaded_a, "cross_world", {"enabled": False})
        assert note is not None and note["enabled"] is False
        loaded_a.usernotes = notes_a
        store.save(loaded_a)
        note_b, notes_b = store.update_usernote(reloaded_b, "cross_world", {"enabled": True})
        assert note_b is not None and note_b["enabled"] is True
        reloaded_b.usernotes = notes_b
        store.save(reloaded_b)

        final_a = store.load("thread_a")
        final_b = store.load("thread_b")
        assert next(n for n in final_a.usernotes if n["id"] == "cross_world")["enabled"] is False
        assert next(n for n in final_b.usernotes if n["id"] == "cross_world")["enabled"] is True

        # --- 4. Editing name/content in one thread is visible in another. ---
        store.update_usernote(final_a, "cross_world", {"name": "renamed", "content": "new content"})
        store.save(final_a)
        reread_b = store.load("thread_b")
        renamed = next(n for n in reread_b.usernotes if n["id"] == "cross_world")
        assert renamed["name"] == "renamed" and renamed["content"] == "new content"

        # --- 5. Delete removes the note everywhere; stale ids are ignored. ---
        deleted, _ = store.delete_usernote(reread_b, "cross_world")
        assert deleted
        store.save(reread_b)
        after_delete_a = store.load("thread_a")
        assert "cross_world" not in {n["id"] for n in after_delete_a.usernotes}
        # Deleting again (stale id) is reported as not-found rather than raising.
        deleted_again, _ = store.delete_usernote(after_delete_a, "cross_world")
        assert not deleted_again

        # --- 6. refresh_out_of_band_fields picks up an enabled toggle saved in between. ---
        stale_snapshot = store.load("thread_a")
        concurrent = store.load("thread_a")
        note, notes = store.update_usernote(concurrent, "graph_note", {"enabled": False})
        assert note is not None and note["enabled"] is False
        concurrent.usernotes = notes
        store.save(concurrent)
        store.refresh_out_of_band_fields(stale_snapshot)
        refreshed_note = next(n for n in stale_snapshot.usernotes if n["id"] == "graph_note")
        assert refreshed_note["enabled"] is False

        # --- 7. A newly created state with enabled_usernote_ids=[] hydrates all-disabled. ---
        fresh = ConversationState(
            thread_id="thread_fresh",
            world_mode="graph",
            world_id="w1",
            enabled_usernote_ids=[],
        )
        fresh.usernotes = store.load_usernotes(fresh)
        assert len(fresh.usernotes) > 0
        assert all(not note["enabled"] for note in fresh.usernotes)

    print("smoke_world_scoping: ok")


if __name__ == "__main__":
    main()
