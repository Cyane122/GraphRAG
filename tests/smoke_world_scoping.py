# ================================
# tests/smoke_world_scoping.py
#
# 월드 모드 격리와 월드 단위 유저노트 공유를 파일 저장소만으로 검증합니다.
#
# Functions
#   - _state(thread_id: str, world_mode: WorldMode, note_id: str) -> ConversationState : 검증용 대화 상태를 만듭니다.
#   - main() -> None : Graph/Wiki 격리와 동일 월드 노트 공유를 검증합니다.
# ================================

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.models import ConversationState, WorldMode  # noqa: E402
from src.apps.app.storage import ConversationStore  # noqa: E402


def _state(thread_id: str, world_mode: WorldMode, note_id: str) -> ConversationState:
    """Create a validation conversation with one legacy per-thread note."""
    return ConversationState(
        thread_id=thread_id,
        world_mode=world_mode,
        world_id="shared_world",
        world_config={"perspective": 3},
        usernotes=[{"id": note_id, "name": note_id, "content": note_id, "enabled": True}],
    )


def main() -> None:
    """Verify shared Graph notes and a separate Wiki namespace."""
    with TemporaryDirectory() as temporary:
        store = ConversationStore(Path(temporary) / "threads")
        graph_a = _state("graph_a", "graph", "legacy_a")
        graph_b = _state("graph_b", "graph", "legacy_b")
        wiki = _state("wiki_a", "wiki", "wiki_only")
        store.save(graph_a)
        store.save(graph_b)
        store.save(wiki)

        migrated = store.load("graph_a")
        assert {note["id"] for note in migrated.usernotes} == {"legacy_a", "legacy_b"}

        shared = store.add_world_usernote(
            migrated,
            {"id": "shared", "name": "shared", "content": "shared", "enabled": True},
        )
        assert "shared" in {note["id"] for note in shared}
        assert "shared" in {note["id"] for note in store.load("graph_b").usernotes}

        wiki_notes = store.load("wiki_a").usernotes
        assert {note["id"] for note in wiki_notes} == {"wiki_only"}
        assert "shared" not in {note["id"] for note in wiki_notes}

        deleted, _ = store.delete_world_usernote(migrated, "shared")
        assert deleted
        assert "shared" not in {note["id"] for note in store.load("graph_b").usernotes}

    print("smoke_world_scoping: ok")


if __name__ == "__main__":
    main()
