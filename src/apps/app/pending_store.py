# ================================
# src/apps/app/pending_store.py
#
# Durable JSON storage for Chainlit pending commits.
#
# Functions
#   - save_pending_commit(pending: dict, world_id: str, pc_id: str, npc_id: str) -> None : Persist one pending commit snapshot.
#   - load_pending_commit(world_id: str, pc_id: str, npc_id: str, thread_id: str | None = None) -> dict | None : Load the latest uncommitted pending snapshot.
#   - discard_pending_commit(pending: dict | None, world_id: str, pc_id: str, npc_id: str) -> None : Remove one pending snapshot.
#   - update_pending_status(pending: dict, world_id: str, pc_id: str, npc_id: str, status: str, failed_stage: str | None = None, failure_reason: str | None = None, completed_stage: str | None = None) -> dict : Update and persist pending commit status.
# ================================

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING_DIR = Path("data") / "pending_commits"


def _safe_part(value: str) -> str:
    """Return a filesystem-safe identifier segment."""
    text = str(value or "unknown").strip() or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def _current_thread_id() -> str:
    """Return the ambient thread id, if any.

    web UI에서는 thread_id가 pending dict에 직접 담겨 전달되므로 ambient 값이 없다(빈 문자열).
    호출부는 `pending.get("thread_id") or _current_thread_id() or "default"` 순으로 사용한다.
    """
    return ""


def _pending_path(pending: dict | None, world_id: str, pc_id: str, npc_id: str) -> Path | None:
    """Build the durable JSON path for one pending commit."""
    commit_id = str((pending or {}).get("commit_id") or "").strip()
    if not commit_id:
        return None
    thread_id = str((pending or {}).get("thread_id") or _current_thread_id() or "default")
    filename = "__".join(
        [
            _safe_part(world_id),
            _safe_part(pc_id),
            _safe_part(npc_id),
            _safe_part(thread_id),
            _safe_part(commit_id),
        ]
    )
    return PENDING_DIR / f"{filename}.json"


def _json_ready(value: Any) -> Any:
    """Convert datetimes and nested containers into JSON-serializable values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def save_pending_commit(pending: dict, world_id: str, pc_id: str, npc_id: str) -> None:
    """Persist one pending commit snapshot."""
    path = _pending_path(pending, world_id, pc_id, npc_id)
    if path is None:
        return
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_ready(pending), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _candidate_paths(world_id: str, pc_id: str, npc_id: str, thread_id: str | None = None) -> list[Path]:
    """Return durable pending files for one world and character pair."""
    if not PENDING_DIR.exists():
        return []
    thread_part = str(thread_id or _current_thread_id() or "").strip()
    if thread_part:
        prefix = "__".join(
            [_safe_part(world_id), _safe_part(pc_id), _safe_part(npc_id), _safe_part(thread_part)]
        )
        pattern = f"{prefix}__*.json"
    else:
        prefix = "__".join([_safe_part(world_id), _safe_part(pc_id), _safe_part(npc_id)])
        pattern = f"{prefix}__*.json"
    return sorted(PENDING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)


def load_pending_commit(world_id: str, pc_id: str, npc_id: str, thread_id: str | None = None) -> dict | None:
    """Load the latest uncommitted pending snapshot."""
    for path in _candidate_paths(world_id, pc_id, npc_id, thread_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") != "committed":
            return payload
    return None


def discard_pending_commit(pending: dict | None, world_id: str, pc_id: str, npc_id: str) -> None:
    """Remove one pending snapshot if it exists."""
    path = _pending_path(pending, world_id, pc_id, npc_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[PendingStore] discard failed: {exc}")


def update_pending_status(
    pending: dict,
    world_id: str,
    pc_id: str,
    npc_id: str,
    status: str,
    failed_stage: str | None = None,
    failure_reason: str | None = None,
    completed_stage: str | None = None,
) -> dict:
    """Update and persist pending commit status."""
    pending["status"] = status
    pending["updated_at"] = datetime.now().isoformat()
    if status == "committed":
        pending["committed_at"] = pending["updated_at"]
        pending["failed_stage"] = None
        pending["failure_reason"] = None
    elif status == "failed":
        pending["failed_stage"] = failed_stage
        pending["failure_reason"] = failure_reason
    else:
        pending["failed_stage"] = None
        pending["failure_reason"] = None
    if completed_stage:
        completed = list(pending.get("completed_stages") or [])
        if completed_stage not in completed:
            completed.append(completed_stage)
        pending["completed_stages"] = completed
    save_pending_commit(pending, world_id, pc_id, npc_id)
    return pending
