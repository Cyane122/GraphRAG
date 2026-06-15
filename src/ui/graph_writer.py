# ================================
# src/ui/graph_writer.py
#
# Graph UI에서 Kuzu DB 노드/엣지 속성을 직접 수정하는 동기 헬퍼입니다.
# 허용 목록 없이 전달된 모든 필드를 SET 시도하고, 스키마 불일치는 조용히 스킵합니다.
#
# Functions
#   - write_node(thread_id, node_id, updates) -> None
#   - write_edge(thread_id, src_node_id, tgt_node_id, updates) -> None
# ================================

from __future__ import annotations

from pathlib import Path
from typing import Any

import kuzu

_THREADS_DIR = Path("data/threads")

# node_id 접두어 → (alias, MATCH 절, match_params 생성 함수)
# match_params_fn(raw_id) -> dict  (쿼리 파라미터)
_TYPE_MAP: dict[str, tuple[str, str, Any]] = {
    "global": (
        "gs",
        "MATCH (gs:GlobalState {id: 'singleton'})",
        lambda _: {},
    ),
    "character": (
        "c",
        "MATCH (c:Character {id: $id})",
        lambda rid: {"id": rid},
    ),
    "state": (
        "d",
        "MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)",
        lambda rid: {"cid": rid},
    ),
    "static": (
        "sp",
        "MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)",
        lambda rid: {"cid": rid},
    ),
    "info": (
        "di",
        "MATCH (c:Character {id: $cid})-[:HAS_INFO]->(di:DynamicInformation)",
        lambda rid: {"cid": rid},
    ),
    "location": (
        "l",
        "MATCH (l:Location {id: $id})",
        lambda rid: {"id": rid},
    ),
    "event": (
        "e",
        "MATCH (e:Event {id: $id})",
        lambda rid: {"id": rid},
    ),
    "memory": (
        "m",
        "MATCH (m:Memory {id: $id})",
        lambda rid: {"id": rid},
    ),
    "personal_fact": (
        "f",
        "MATCH (f:PersonalFact {id: $id})",
        lambda rid: {"id": rid},
    ),
}

# 쓰기 금지 필드 (그래프 내부 식별자)
_SKIP_FIELDS = {"_id", "_label", "_offset", "_table"}


def _open(thread_id: str) -> tuple[kuzu.Database, kuzu.Connection]:
    db = kuzu.Database(str(_THREADS_DIR / thread_id / "schema"))
    return db, kuzu.Connection(db)


def _close(db: kuzu.Database, conn: kuzu.Connection) -> None:
    for obj in (conn, db):
        try:
            obj.close()
        except Exception:
            pass


def _coerce(value: Any) -> Any:
    """문자열로 넘어온 숫자를 Kuzu가 받을 수 있는 타입으로 변환합니다."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    # bool
    if stripped.lower() in {"true", "false"}:
        return stripped.lower() == "true"
    # int
    try:
        return int(stripped)
    except ValueError:
        pass
    # float
    try:
        return float(stripped)
    except ValueError:
        pass
    return value


def write_node(thread_id: str, node_id: str, updates: dict[str, Any]) -> None:
    """node_id 형식(예: 'state:alice')을 파싱해 updates의 모든 필드를 Kuzu에 반영합니다.
    스키마에 없는 필드나 타입 불일치는 조용히 스킵합니다."""
    if not updates:
        return

    parts = node_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid node_id: {node_id!r}")
    kind, raw_id = parts

    cfg = _TYPE_MAP.get(kind)
    if cfg is None:
        raise ValueError(f"Unsupported node kind: {kind!r}")

    alias, match_clause, params_fn = cfg
    base_params = params_fn(raw_id)

    db, conn = _open(thread_id)
    try:
        errors = []
        for field, raw_val in updates.items():
            if field in _SKIP_FIELDS:
                continue
            value = _coerce(raw_val)
            try:
                conn.execute(
                    f"{match_clause} SET {alias}.{field} = $val",
                    {**base_params, "val": value},
                )
            except Exception as exc:
                errors.append(f"{field}: {exc}")
        if errors and len(errors) == len(updates):
            raise RuntimeError("모든 필드 쓰기 실패: " + "; ".join(errors))
    finally:
        _close(db, conn)


def write_edge(thread_id: str, src_node_id: str, tgt_node_id: str, updates: dict[str, Any]) -> None:
    """character→character RELATIONSHIP 엣지 속성을 업데이트합니다."""
    if not updates:
        return

    src_parts = src_node_id.split(":", 1)
    tgt_parts = tgt_node_id.split(":", 1)
    if src_parts[0] != "character" or tgt_parts[0] != "character":
        raise ValueError("Only character→character RELATIONSHIP edges are writable")

    src_id, tgt_id = src_parts[1], tgt_parts[1]
    db, conn = _open(thread_id)
    try:
        for field, raw_val in updates.items():
            if field in _SKIP_FIELDS:
                continue
            value = _coerce(raw_val)
            try:
                conn.execute(
                    f"MATCH (a:Character {{id: $src}})-[r:RELATIONSHIP]->(b:Character {{id: $tgt}}) SET r.{field} = $val",
                    {"src": src_id, "tgt": tgt_id, "val": value},
                )
            except Exception:
                pass
    finally:
        _close(db, conn)
