# ================================
# tests/smoke_wiki_thread_migration.py
#
# 기존 Wiki thread 상태 계약 migration의 미리보기·감사·충돌·inverse를 검증합니다.
#
# Functions
#   - _character_document(thread_id: str, include_current_state: bool = True) -> str : legacy character fixture를 렌더링합니다.
#   - _write_character(vault_root: Path, thread_id: str, content: str) -> Path : thread fixture를 기록합니다.
#   - main() -> None : migration smoke assertions를 실행합니다.
# ================================

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from src.apps.app.app import create_app
from src.wiki import (
    PendingWikiCommit,
    WikiCommitQueue,
    WikiStore,
    apply_thread_contract_migration,
    plan_thread_contract_migration,
)


def _character_document(thread_id: str, include_current_state: bool = True) -> str:
    """기존 정적·동적 내용을 가진 최소 character Markdown을 반환합니다."""
    current_state = """
## 현재 상태

### 현재 위치와 활동

- 위치: 기존 장소
- 활동: 기존 행동

### 신체 상태와 감정 상태

- 신체 상태: 안정
- 감정 상태: 평온
""" if include_current_state else ""
    return f"""---
id: character:{thread_id}:legacy
type: character
schema_version: 1
visibility: [actor, updater, player]
created_at: 2026-07-24T00:00:00+00:00
world_id: demo_world
thread_id: {thread_id}
profile_id: character_profile:legacy
---
# Legacy Character

## 정적 설정

- Preserve this exact canon.
{current_state}"""


def _write_character(vault_root: Path, thread_id: str, content: str) -> Path:
    """테스트 thread에 character Markdown 하나를 만들고 경로를 반환합니다."""
    path = vault_root / "threads" / thread_id / "characters" / "legacy.md"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> None:
    """Migration preview, apply, audit, inverse, idempotence, conflict를 검증합니다."""
    route_paths = {route.path for route in create_app().routes}
    assert "/api/conversations/{thread_id}/wiki/migration" in route_paths
    assert "/api/conversations/{thread_id}/wiki/migration/apply" in route_paths

    with TemporaryDirectory() as temporary:
        vault_root = Path(temporary) / "wiki_v2"
        thread_id = "legacy_thread"
        original = _character_document(thread_id)
        character_path = _write_character(vault_root, thread_id, original)

        preview = plan_thread_contract_migration(vault_root, thread_id)
        assert preview.status == "ready"
        assert preview.changed_documents == ["characters/legacy.md"]
        assert len(preview.patches) == 1
        assert character_path.read_text(encoding="utf-8") == original

        result = apply_thread_contract_migration(vault_root, thread_id)
        assert result.status == "applied"
        assert result.migration_commit_id
        migrated = character_path.read_text(encoding="utf-8")
        assert "- Preserve this exact canon." in migrated
        assert "### 욕구와 컨디션" in migrated
        assert "### Personality Change Ledger" in migrated
        assert "### Reproductive State" in migrated
        assert "- Contraception: none" in migrated

        queue = WikiCommitQueue(WikiStore(vault_root / "threads" / thread_id))
        archive = queue.load_archive(result.migration_commit_id)
        assert archive.operation == "manual"
        assert archive.status == "applied"
        assert len(archive.applied_changes) == 1
        assert archive.applied_changes[0].before_markdown in original

        inverse = queue.apply_inverse(result.migration_commit_id)
        assert inverse.status == "applied"
        assert character_path.read_text(encoding="utf-8").strip() == original.strip()
        assert plan_thread_contract_migration(vault_root, thread_id).status == "ready"

        reapplied = apply_thread_contract_migration(vault_root, thread_id)
        assert reapplied.status == "applied"
        assert plan_thread_contract_migration(vault_root, thread_id).status == "up_to_date"

        pending = PendingWikiCommit(
            user_input_hash=sha256(b"pending-user").hexdigest(),
            actor_response_hash=sha256(b"pending-actor").hexdigest(),
            updater_model="test",
        )
        queue.queue(pending)
        blocked = plan_thread_contract_migration(vault_root, thread_id)
        assert blocked.status == "conflict"
        assert queue.load() == pending
        queue.skip_pending("migration smoke cleanup")

        malformed_id = "missing_state"
        malformed = _character_document(malformed_id, include_current_state=False)
        malformed_path = _write_character(vault_root, malformed_id, malformed)
        malformed_plan = plan_thread_contract_migration(vault_root, malformed_id)
        assert malformed_plan.status == "conflict"
        assert malformed_path.read_text(encoding="utf-8") == malformed

    print("Wiki thread migration smoke checks passed.")


if __name__ == "__main__":
    main()
