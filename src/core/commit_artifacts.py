# ================================
# src/core/commit_artifacts.py
#
# Commit-scoped JSON artifacts for planner and extractor shadow runs.
#
# Functions
#   - text_hash(text: str) -> str : Return a stable SHA-256 hash for artifact reuse checks.
#   - artifact_dir(thread_id: str | None, commit_id: str) -> Path : Return the commit artifact directory.
#   - read_artifact(thread_id: str | None, commit_id: str, filename: str) -> dict | None : Read a commit artifact JSON file.
#   - write_artifact(thread_id: str | None, commit_id: str, filename: str, payload: dict) -> Path | None : Write a commit artifact JSON file.
# ================================

import hashlib
import json
import re
from pathlib import Path


_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def text_hash(text: str) -> str:
    """Return a stable SHA-256 hash for text stored in commit artifacts."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _safe_part(value: str | None, fallback: str) -> str:
    """Return a filesystem-safe path component."""
    raw = str(value or "").strip() or fallback
    safe = _SAFE_PART_RE.sub("_", raw).strip("._")
    return safe or fallback


def artifact_dir(thread_id: str | None, commit_id: str) -> Path:
    """Return the directory for one thread/commit artifact bundle."""
    return (
        Path("data")
        / "threads"
        / _safe_part(thread_id, "unknown_thread")
        / "commit_artifacts"
        / _safe_part(commit_id, "unknown_commit")
    )


def read_artifact(thread_id: str | None, commit_id: str, filename: str) -> dict | None:
    """Read an artifact JSON object if it exists and is valid."""
    path = artifact_dir(thread_id, commit_id) / filename
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[CommitArtifact] read failed for {path}: {exc}")
        return None


def write_artifact(thread_id: str | None, commit_id: str, filename: str, payload: dict) -> Path | None:
    """Write an artifact JSON object and return its path on success."""
    path = artifact_dir(thread_id, commit_id) / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path
    except OSError as exc:
        print(f"[CommitArtifact] write failed for {path}: {exc}")
        return None
