# ================================
# src/core/data_layer/json_data_layer.py
#
# JSON 파일 기반 Chainlit 데이터 레이어.
# 스레드(채팅방)와 스텝(메시지)을 data/threads/ 에 저장합니다.
#
# Classes
#   - JsonDataLayer : BaseDataLayer 구현체. data/threads/<id>.json 에 스레드를 저장
# ================================

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from chainlit.data.base import BaseDataLayer
from chainlit.data.utils import queue_until_user_message
from chainlit.types import (
    Feedback,
    PageInfo,
    PaginatedResponse,
    Pagination,
    ThreadDict,
    ThreadFilter,
)
from chainlit.user import PersistedUser, User

_DATA_DIR = Path("data")
_INDEX_FILE = _DATA_DIR / "index.json"
_THREADS_DIR = _DATA_DIR / "threads"
_LOCK = asyncio.Lock()

_DEFAULT_USER_ID = "local"
_DEFAULT_USER_IDENTIFIER = "local"


def _now_iso() -> str:
    """현재 시각을 ISO 8601 문자열로 반환합니다."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    """저장 디렉터리가 없으면 생성합니다."""
    _DATA_DIR.mkdir(exist_ok=True)
    _THREADS_DIR.mkdir(exist_ok=True)


def _read_index() -> dict:
    """인덱스 파일을 읽습니다. 없거나 손상되면 빈 인덱스를 반환합니다."""
    if not _INDEX_FILE.exists():
        return {"threads": []}
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"threads": []}


def _write_index(index: dict) -> None:
    """인덱스 파일을 갱신합니다."""
    _ensure_dirs()
    _INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _thread_path(thread_id: str) -> Path:
    """스레드 ID에 대응하는 JSON 파일 경로를 반환합니다."""
    return _THREADS_DIR / thread_id / "chat.json"


def _read_thread(thread_id: str) -> dict | None:
    """스레드 파일을 읽습니다. 없거나 손상되면 None을 반환합니다."""
    path = _thread_path(thread_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_thread(thread: dict) -> None:
    """스레드를 JSON 파일로 저장합니다."""
    _ensure_dirs()
    path = _thread_path(thread["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(thread, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ensure_thread(thread_id: str) -> dict:
    """스레드 파일이 없으면 빈 스레드를 생성합니다.

    인덱스(사이드바) 등록은 하지 않습니다.
    인덱스 등록은 첫 번째 사용자 메시지 시점에 update_thread가 담당합니다.
    """
    thread = _read_thread(thread_id)
    if thread is not None:
        return thread

    thread = {
        "id": thread_id,
        "name": "새 채팅",
        "createdAt": _now_iso(),
        "userId": _DEFAULT_USER_ID,
        "userIdentifier": _DEFAULT_USER_IDENTIFIER,
        "tags": [],
        "metadata": {},
        "steps": [],
        "elements": [],
    }
    _write_thread(thread)
    return thread


class JsonDataLayer(BaseDataLayer):
    """JSON 파일 기반 Chainlit 데이터 레이어."""

    # ── 사용자 ────────────────────────────────────────────────────────────
    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        """항상 로컬 기본 사용자를 반환합니다."""
        return PersistedUser(
            id=_DEFAULT_USER_ID,
            identifier=_DEFAULT_USER_IDENTIFIER,
            createdAt=_now_iso(),
        )

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        """로컬 기본 사용자를 반환합니다 (실제 생성 없음)."""
        return PersistedUser(
            id=_DEFAULT_USER_ID,
            identifier=_DEFAULT_USER_IDENTIFIER,
            createdAt=_now_iso(),
        )

    # ── 피드백 (no-op) ────────────────────────────────────────────────────
    async def delete_feedback(self, feedback_id: str) -> bool:
        """피드백 삭제는 지원하지 않습니다."""
        return True

    async def upsert_feedback(self, feedback: Feedback) -> str:
        """피드백 저장은 지원하지 않습니다."""
        return str(uuid.uuid4())

    # ── 즐겨찾기 (no-op) ──────────────────────────────────────────────────
    async def get_favorite_steps(self, user_id: str) -> list:
        """즐겨찾기는 지원하지 않습니다."""
        return []

    # ── 엘리먼트 (최소 구현) ──────────────────────────────────────────────
    @queue_until_user_message()
    async def create_element(self, element: object) -> None:
        """엘리먼트(파일 첨부) 저장은 지원하지 않습니다."""

    async def get_element(self, thread_id: str, element_id: str) -> None:
        """엘리먼트 조회는 지원하지 않습니다."""
        return None

    @queue_until_user_message()
    async def delete_element(self, element_id: str, thread_id: Optional[str] = None) -> None:
        """엘리먼트 삭제는 지원하지 않습니다."""

    # ── 스텝(메시지) ──────────────────────────────────────────────────────
    @staticmethod
    def _strip_orphan_parent(step_dict: dict, valid_ids: set[str]) -> dict:
        """parentId가 저장된 step에 없으면 제거해 root-level step으로 만든다."""
        parent_id = step_dict.get("parentId")
        if parent_id and parent_id not in valid_ids:
            step_dict = dict(step_dict)
            step_dict["parentId"] = None
        return step_dict

    async def create_step(self, step_dict: dict) -> None:
        """메시지(스텝)를 스레드 JSON에 저장합니다."""
        if step_dict.get("type") == "run":
            return
        async with _LOCK:
            thread_id = step_dict.get("threadId")
            if not thread_id:
                return
            thread = _ensure_thread(thread_id)
            if not any(s["id"] == step_dict["id"] for s in thread["steps"]):
                valid_ids = {s["id"] for s in thread["steps"]}
                thread["steps"].append(self._strip_orphan_parent(dict(step_dict), valid_ids))
                _write_thread(thread)

    async def update_step(self, step_dict: dict) -> None:
        """스텝 내용을 갱신합니다 (스트리밍 완료 후 최종본 저장)."""
        if step_dict.get("type") == "run":
            return
        async with _LOCK:
            thread_id = step_dict.get("threadId")
            if not thread_id:
                return
            thread = _ensure_thread(thread_id)
            valid_ids = {s["id"] for s in thread["steps"]}
            step_dict = self._strip_orphan_parent(dict(step_dict), valid_ids)
            for i, step in enumerate(thread["steps"]):
                if step["id"] == step_dict["id"]:
                    thread["steps"][i] = step_dict
                    break
            else:
                thread["steps"].append(step_dict)
            _write_thread(thread)

    @queue_until_user_message()
    async def delete_step(self, step_id: str) -> None:
        """스텝(메시지)을 삭제합니다. 전체 스레드 파일을 탐색합니다."""
        async with _LOCK:
            _ensure_dirs()
            for path in _THREADS_DIR.glob("*/chat.json"):
                try:
                    thread = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                original = len(thread["steps"])
                thread["steps"] = [s for s in thread["steps"] if s["id"] != step_id]
                if len(thread["steps"]) < original:
                    path.write_text(
                        json.dumps(thread, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    break

    # ── 스레드(채팅방) ────────────────────────────────────────────────────
    async def get_thread_author(self, thread_id: str) -> str:
        """스레드 소유자 ID를 반환합니다."""
        thread = _read_thread(thread_id)
        return thread.get("userId", _DEFAULT_USER_ID) if thread else _DEFAULT_USER_ID

    async def delete_thread(self, thread_id: str) -> None:
        """스레드 폴더(chat.json + schema/)와 인덱스 항목을 삭제합니다."""
        import shutil
        async with _LOCK:
            thread_dir = _THREADS_DIR / thread_id
            if thread_dir.exists():
                shutil.rmtree(thread_dir, ignore_errors=True)
            index = _read_index()
            index["threads"] = [t for t in index["threads"] if t["id"] != thread_id]
            _write_index(index)

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        """사이드바용 스레드 목록을 최신 순으로 반환합니다."""
        index = _read_index()
        all_threads = sorted(
            index.get("threads", []),
            key=lambda t: t.get("createdAt", ""),
            reverse=True,
        )

        # cursor 기반 페이지네이션
        page_size = pagination.first or 20
        start = 0
        if pagination.cursor:
            for i, t in enumerate(all_threads):
                if t["id"] == pagination.cursor:
                    start = i + 1
                    break

        page = all_threads[start : start + page_size]
        has_next = (start + page_size) < len(all_threads)

        thread_dicts: List[ThreadDict] = []
        for meta in page:
            full = _read_thread(meta["id"])
            if full:
                thread_dicts.append(
                    ThreadDict(
                        id=full["id"],
                        createdAt=full["createdAt"],
                        name=full.get("name"),
                        userId=full.get("userId"),
                        userIdentifier=full.get("userIdentifier"),
                        tags=full.get("tags", []),
                        metadata=full.get("metadata", {}),
                        steps=full.get("steps", []),
                        elements=full.get("elements", []),
                    )
                )

        return PaginatedResponse(
            data=thread_dicts,
            pageInfo=PageInfo(
                hasNextPage=has_next,
                startCursor=page[0]["id"] if page else None,
                endCursor=page[-1]["id"] if page else None,
            ),
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        """스레드 전체 데이터를 반환합니다 (채팅방 재개 시 호출됨)."""
        thread = _read_thread(thread_id)
        if not thread:
            return None
        steps = thread.get("steps", [])
        valid_ids = {s["id"] for s in steps}
        steps = [self._strip_orphan_parent(s, valid_ids) for s in steps]
        return ThreadDict(
            id=thread["id"],
            createdAt=thread["createdAt"],
            name=thread.get("name"),
            userId=thread.get("userId"),
            userIdentifier=thread.get("userIdentifier"),
            tags=thread.get("tags", []),
            metadata=thread.get("metadata", {}),
            steps=steps,
            elements=thread.get("elements", []),
        )

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """스레드를 생성하거나 이름/메타데이터를 갱신합니다."""
        async with _LOCK:
            thread = _read_thread(thread_id)
            if thread is None:
                thread = {
                    "id": thread_id,
                    "name": name or "새 채팅",
                    "createdAt": _now_iso(),
                    "userId": user_id or _DEFAULT_USER_ID,
                    "userIdentifier": _DEFAULT_USER_IDENTIFIER,
                    "tags": tags or [],
                    "metadata": metadata or {},
                    "steps": [],
                    "elements": [],
                }
            else:
                if name is not None:
                    thread["name"] = name
                if user_id is not None:
                    thread["userId"] = user_id
                if metadata is not None:
                    thread["metadata"] = metadata
                if tags is not None:
                    thread["tags"] = tags
            _write_thread(thread)

            # 인덱스 동기화
            index = _read_index()
            existing = next((t for t in index["threads"] if t["id"] == thread_id), None)
            if existing is None:
                index["threads"].append(
                    {
                        "id": thread_id,
                        "name": thread["name"],
                        "createdAt": thread["createdAt"],
                        "userId": thread["userId"],
                    }
                )
            else:
                existing["name"] = thread["name"]
            _write_index(index)

    async def build_debug_url(self) -> str:
        """디버그 URL은 지원하지 않습니다."""
        return ""

    async def close(self) -> None:
        """리소스 정리는 필요하지 않습니다."""
