# ================================
# src/ui/graph_loader.py
#
# Chainlit 세션 없이 특정 스레드의 Kuzu DB에서 직접 그래프 스냅샷을 생성합니다.
# graph_server.py의 /api/load 엔드포인트에서 호출합니다.
#
# Functions
#   - list_threads() -> list[dict[str, str]] : DB가 있는 스레드 목록 (최근 수정순)
#   - get_latest_thread_id() -> str | None : 최신 스레드 ID
#   - get_thread_schema(thread_id: str) -> list[dict] : 스레드 Kuzu DB의 테이블 스키마 정보 반환
#   - build_graph_from_thread(thread_id: str) -> GraphSnapshot : 스레드 DB 스냅샷 생성
# ================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import kuzu

from src.ui.graph_models import GraphEdge, GraphNode, GraphSnapshot

_THREADS_DIR = Path("data/threads")
_INDEX_FILE = Path("data/index.json")


# ── Thread discovery ─────────────────────────────────────────────────────────

def _iso_from_mtime(path: Path) -> str:
    """파일/디렉터리 수정 시각을 UTC ISO 문자열로 반환합니다."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def _thread_modified_at(thread_id: str) -> str:
    """스레드의 실제 최근 변경 시각을 파일 시스템에서 계산합니다."""
    thread_dir = _THREADS_DIR / thread_id
    candidates = [
        thread_dir,
        thread_dir / "chat.json",
        thread_dir / "usernote.md",
        thread_dir / "schema",
    ]
    return max((_iso_from_mtime(path) for path in candidates if path.exists()), default="")


def _load_index_threads() -> dict[str, dict[str, Any]]:
    """data/index.json의 스레드 항목을 id 기준 dict로 읽습니다."""
    if not _INDEX_FILE.exists():
        return {}
    try:
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        str(thread.get("id", "")): thread
        for thread in data.get("threads", [])
        if thread.get("id")
    }


def _read_thread_record(thread_id: str, index_threads: dict[str, dict[str, Any]]) -> dict[str, str]:
    """인덱스와 chat.json, 파일 수정 시각을 합쳐 목록 표시용 레코드를 만듭니다."""
    record = dict(index_threads.get(thread_id, {}))
    chat_path = _THREADS_DIR / thread_id / "chat.json"
    if chat_path.exists():
        try:
            chat = json.loads(chat_path.read_text(encoding="utf-8"))
            record = {**chat, **record}
        except Exception:
            pass

    name = str(record.get("name", "") or thread_id)
    created_at = str(record.get("createdAt", "") or "")
    modified_at = _thread_modified_at(thread_id) or created_at
    return {
        "id": thread_id,
        "name": name[:60].replace("\n", " "),
        "createdAt": created_at,
        "modifiedAt": modified_at,
    }


def list_threads() -> list[dict[str, str]]:
    """DB 파일이 있는 스레드 목록을 최근 수정순으로 반환합니다."""
    index_threads = _load_index_threads()
    thread_ids = set(index_threads)
    if _THREADS_DIR.exists():
        thread_ids.update(path.name for path in _THREADS_DIR.iterdir() if path.is_dir())

    result = [
        _read_thread_record(thread_id, index_threads)
        for thread_id in thread_ids
        if (_THREADS_DIR / thread_id / "schema").exists()
    ]
    return sorted(
        result,
        key=lambda thread: thread.get("modifiedAt") or thread.get("createdAt") or "",
        reverse=True,
    )


def get_latest_thread_id() -> str | None:
    """DB 파일이 있는 가장 최신 스레드 ID를 반환합니다."""
    threads = list_threads()
    return threads[0]["id"] if threads else None


def _read_thread_meta(thread_id: str) -> dict[str, Any]:
    """스레드 chat.json에서 메타데이터를 읽습니다."""
    path = _THREADS_DIR / thread_id / "chat.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("metadata", {})
    except Exception:
        return {}


# ── Sync Kuzu helpers ─────────────────────────────────────────────────────────

def _exec(conn: kuzu.Connection, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """동기 쿼리를 실행하고 결과를 dict 리스트로 반환합니다. 테이블 부재 등의 오류는 무시합니다."""
    try:
        qr = conn.execute(query, params or {})
        col_names = qr.get_column_names()
        rows: list[dict[str, Any]] = []
        while qr.has_next():
            rows.append(dict(zip(col_names, qr.get_next())))
        qr.close()
        return rows
    except Exception:
        return []


def _clean(value: Any) -> Any:
    """Kuzu 반환값을 JSON 직렬화 가능한 값으로 변환합니다."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean(v) for v in value]
    try:
        data = dict(value)
        if "props" in data and isinstance(data["props"], str):
            try:
                props = json.loads(data["props"])
                if isinstance(props, dict):
                    data = {k: v for k, v in data.items() if k != "props"}
                    data = {**data, **props}
            except json.JSONDecodeError:
                pass
        return {k: _clean(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return str(value)


def _compact(obj: Any) -> dict[str, Any]:
    """None / 빈 값을 제거한 dict를 반환합니다."""
    data = _clean(obj)
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def _ensure_event_node(
    conn: kuzu.Connection,
    nodes: list[GraphNode],
    existing_node_ids: set[str],
    event_id: str,
) -> None:
    """Memory 연결에 필요한 Event 노드가 없으면 DB에서 조회해 추가합니다."""
    event_node_id = f"event:{event_id}"
    if not event_id or event_node_id in existing_node_ids:
        return

    event_rows = _exec(conn, """
        MATCH (e:Event {id: $event_id})
        RETURN e.id AS id, e.summary AS summary, e.timestamp AS timestamp,
               e.importance AS importance, e.content AS content, e.status AS status,
               e.turn_count AS turn_count, e.memory_type AS memory_type,
               e.narrative_summary AS narrative_summary, e.state_summary AS state_summary
    """, {"event_id": event_id})
    event = event_rows[0] if event_rows else {"id": event_id}
    nodes.append(GraphNode(
        id=event_node_id,
        label=event.get("summary") or event.get("content") or event_id,
        type="event",
        subtitle=event.get("timestamp") or event.get("status") or "",
        details=_compact(event),
    ))
    existing_node_ids.add(event_node_id)


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _schema_from_connection(conn: kuzu.Connection) -> list[dict]:
    """열린 Kuzu 연결에서 테이블 스키마 정보를 반환합니다."""
    tables = _exec(conn, "CALL show_tables() RETURN name, type, comment")
    result = []
    for table in tables:
        name = table.get("name", "")
        if not name:
            continue
        cols = _exec(conn, f"CALL table_info('{name}') RETURN property_id, name, type, default_expression")
        result.append({
            "name": name,
            "type": str(table.get("type", "")),
            "comment": str(table.get("comment", "") or ""),
            "columns": [
                {"name": c.get("name", ""), "type": str(c.get("type", ""))}
                for c in cols
                if isinstance(c, dict) and c.get("name")
            ],
        })
    return sorted(result, key=lambda t: (t.get("type", ""), t.get("name", "")))


def _schema_from_thread_definition(thread_id: str) -> list[dict]:
    """스레드 메타데이터의 world/scenario 정의에서 스키마 정보를 재구성합니다."""
    meta = _read_thread_meta(thread_id)
    world_id = str(meta.get("world_id") or "")
    if not world_id:
        return []
    scenario_id = meta.get("scenario_id")
    try:
        from src.ui.web_app.world_state import fetch_world_definition_schema

        return fetch_world_definition_schema(world_id, str(scenario_id) if scenario_id else None)
    except Exception:
        return []


def get_thread_schema(thread_id: str) -> list[dict]:
    """스레드 Kuzu DB의 테이블 스키마 정보를 반환합니다.

    DB가 lock 중이면 world/scenario 정의 스키마로 폴백합니다.
    """
    db_path = str(_THREADS_DIR / thread_id / "schema")
    if not Path(db_path).exists():
        return _schema_from_thread_definition(thread_id)
    try:
        db = kuzu.Database(db_path, read_only=True)
        conn = kuzu.Connection(db)
        try:
            return _schema_from_connection(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        return _schema_from_thread_definition(thread_id)


def build_graph_from_thread(thread_id: str) -> GraphSnapshot:
    """특정 스레드의 Kuzu DB에서 그래프 스냅샷을 생성합니다."""
    db_path = str(_THREADS_DIR / thread_id / "schema")
    meta = _read_thread_meta(thread_id)
    world_id = meta.get("world_id", "unknown")

    db = kuzu.Database(db_path, read_only=True)
    conn = kuzu.Connection(db)
    try:
        snapshot = _build_snapshot(conn, world_id)
        snapshot.schema_ = _schema_from_connection(conn)
        snapshot.thread_id = thread_id
        return snapshot
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


def _build_snapshot(conn: kuzu.Connection, world_id: str) -> GraphSnapshot:
    """열린 Kuzu 연결에서 그래프 뷰어용 스냅샷을 조립합니다."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # ── Global State ──────────────────────────────────────────────────────────
    gs_rows = _exec(conn, """
        MATCH (gs:GlobalState {id: 'singleton'})
        RETURN gs.currentTime AS currentTime,
               gs.currentLocationId AS currentLocationId,
               gs.weather AS weather,
               gs.schedule_slot AS schedule_slot
    """)
    gs = gs_rows[0] if gs_rows else {}
    current_time: str | None = gs.get("currentTime")
    current_location_id: str | None = gs.get("currentLocationId")

    nodes.append(GraphNode(
        id="global:singleton",
        label="Global State",
        type="global",
        subtitle=str(current_time or ""),
        details=_compact(gs),
    ))

    # ── Characters ────────────────────────────────────────────────────────────
    char_rows = _exec(conn, """
        MATCH (c:Character)
        OPTIONAL MATCH (c)-[:LOCATED_AT]->(l:Location)
        OPTIONAL MATCH (c)-[:HAS_STATE]->(d:DynamicState)
        OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
        OPTIONAL MATCH (c)-[:HAS_INFO]->(di:DynamicInformation)
        RETURN c.id AS id, c.name AS name, c.type AS type,
               l.id AS location_id, l.name AS location_name,
               d.location_id AS state_location_id,
               d AS state, sp AS static_profile, di AS dynamic_info
    """)
    char_by_id = {r["id"]: r for r in char_rows if r.get("id")}

    included_location_ids: set[str] = set()
    if current_location_id:
        included_location_ids.add(current_location_id)

    for char_id, row in char_by_id.items():
        char_node_id = f"character:{char_id}"
        char_type = row.get("type") or "Character"
        loc_id = row.get("location_id") or row.get("state_location_id")
        if loc_id:
            included_location_ids.add(loc_id)

        nodes.append(GraphNode(
            id=char_node_id,
            label=row.get("name") or char_id,
            type="character",
            subtitle=char_type,
            details={
                "id": char_id,
                "type": char_type,
                "location": row.get("location_name") or loc_id,
            },
        ))
        if loc_id:
            edges.append(GraphEdge(source=char_node_id, target=f"location:{loc_id}", label="LOCATED_AT"))

        state = _clean(row.get("state"))
        if isinstance(state, dict) and state:
            state_id = f"state:{char_id}"
            state_sub = state.get("physical_condition") or state.get("mood") or state.get("mental_condition") or ""
            nodes.append(GraphNode(id=state_id, label=f"{row.get('name') or char_id} state", type="state", subtitle=str(state_sub), details=_compact(state)))
            edges.append(GraphEdge(source=char_node_id, target=state_id, label="HAS_STATE"))

        static = _clean(row.get("static_profile"))
        if isinstance(static, dict) and static:
            static_id = f"static:{char_id}"
            static_sub = static.get("role") or static.get("grade_class") or static.get("nationality") or ""
            nodes.append(GraphNode(id=static_id, label=f"{row.get('name') or char_id} StaticInformation", type="static_information", subtitle=str(static_sub), details=_compact(static)))
            edges.append(GraphEdge(source=char_node_id, target=static_id, label="HAS_PROFILE"))

        info = _clean(row.get("dynamic_info"))
        if isinstance(info, dict) and info:
            info_id = f"info:{char_id}"
            nodes.append(GraphNode(id=info_id, label=f"{row.get('name') or char_id} DynamicInformation", type="dynamic_information", subtitle="", details=_compact(info)))
            edges.append(GraphEdge(source=char_node_id, target=info_id, label="HAS_INFO"))

    # ── Locations ─────────────────────────────────────────────────────────────
    for loc_id in sorted(included_location_ids):
        loc_rows = _exec(conn, """
            MATCH (l:Location {id: $lid})
            OPTIONAL MATCH (c:Character)-[:LOCATED_AT]->(l)
            RETURN l.id AS id, l.name AS name, l.summary AS summary,
                   collect(c.id) AS current_chars
        """, {"lid": loc_id})
        if loc_rows:
            loc = loc_rows[0]
            loc_node_id = f"location:{loc_id}"
            nodes.append(GraphNode(
                id=loc_node_id,
                label=loc.get("name") or loc_id,
                type="location",
                subtitle=loc_id,
                details=_compact(loc),
            ))
            if loc_id == current_location_id:
                edges.append(GraphEdge(source="global:singleton", target=loc_node_id, label="current"))

    # ── Relationships ─────────────────────────────────────────────────────────
    rel_rows = _exec(conn, """
        MATCH (a:Character)-[r:RELATIONSHIP]->(b:Character)
        RETURN a.id AS source, b.id AS target,
               r.type AS type, r.affinity AS affinity, r.trust AS trust,
               r.duration AS duration, r.origin AS origin,
               r.current_status AS current_status,
               r.last_interaction AS last_interaction
    """)
    for rel in rel_rows:
        source, target = rel.get("source"), rel.get("target")
        if source not in char_by_id or target not in char_by_id:
            continue
        aff = rel.get("affinity")
        trust = rel.get("trust")
        bits = ["REL"]
        if aff is not None:
            bits.append(f"aff {aff}")
        if trust is not None:
            bits.append(f"trust {trust}")
        details = {k: v for k, v in rel.items() if v not in (None, "", [], {})}
        edges.append(GraphEdge(source=f"character:{source}", target=f"character:{target}", label=" / ".join(bits), details=details))

    # ── Events ────────────────────────────────────────────────────────────────
    event_rows = _exec(conn, """
        MATCH (c:Character)-[:INVOLVED_IN]->(e:Event)
        RETURN c.id AS char_id, e.id AS id, e.summary AS summary,
               e.timestamp AS timestamp, e.importance AS importance
        ORDER BY e.timestamp DESC
        LIMIT 20
    """)
    seen_events: set[str] = set()
    for ev in event_rows:
        event_id = ev.get("id")
        if not event_id:
            continue
        char_id = ev.get("char_id")
        if event_id not in seen_events:
            seen_events.add(event_id)
            nodes.append(GraphNode(
                id=f"event:{event_id}",
                label=ev.get("summary") or event_id,
                type="event",
                subtitle=ev.get("timestamp") or "",
                details={k: v for k, v in ev.items() if v not in (None, "", [], {}) and k != "char_id"},
            ))
        if char_id in char_by_id:
            edges.append(GraphEdge(source=f"character:{char_id}", target=f"event:{event_id}", label="INVOLVED_IN"))

    latest_events = _exec(conn, """
        MATCH (e:Event)
        RETURN e.id AS id
        ORDER BY e.timestamp DESC
        LIMIT 20
    """)
    existing_node_ids = {node.id for node in nodes}
    for event in latest_events:
        event_id = event.get("id")
        if event_id:
            _ensure_event_node(conn, nodes, existing_node_ids, str(event_id))

    # ── Memories ──────────────────────────────────────────────────────────────
    memory_rows = _exec(conn, """
        MATCH (c:Character)-[:REMEMBERS]->(m:Memory)
        OPTIONAL MATCH (m)-[:OF_EVENT]->(e:Event)
        RETURN c.id AS char_id, m.id AS id, m.event_id AS event_id,
               e.id AS linked_event_id, m.summary AS summary,
               m.memory_type AS memory_type, m.importance AS importance,
               m.distortion_level AS distortion_level,
               m.summary_level AS summary_level,
               m.created_at AS created_at, m.last_decayed_at AS last_decayed_at,
               m.narrative_summary AS narrative_summary,
               m.state_summary AS state_summary
        ORDER BY m.created_at DESC
        LIMIT 40
    """)
    seen_memories: set[str] = set()
    seen_memory_edges: set[str] = set()
    for memory in memory_rows:
        memory_id = memory.get("id")
        char_id = memory.get("char_id")
        if not memory_id:
            continue
        memory_node_id = f"memory:{memory_id}"
        if memory_id not in seen_memories:
            seen_memories.add(memory_id)
            nodes.append(GraphNode(
                id=memory_node_id,
                label=memory.get("summary") or memory_id,
                type="memory",
                subtitle=memory.get("created_at") or memory.get("memory_type") or "",
                details={k: v for k, v in memory.items() if v not in (None, "", [], {}) and k != "char_id"},
            ))
            existing_node_ids.add(memory_node_id)
        remembers_key = f"{char_id}:{memory_id}:remembers"
        if char_id in char_by_id and remembers_key not in seen_memory_edges:
            seen_memory_edges.add(remembers_key)
            edges.append(GraphEdge(source=f"character:{char_id}", target=memory_node_id, label="REMEMBERS"))
        linked_event_id = memory.get("linked_event_id") or memory.get("event_id")
        event_node_id = f"event:{linked_event_id}" if linked_event_id else ""
        of_event_key = f"{memory_id}:{linked_event_id}:of_event"
        if linked_event_id:
            _ensure_event_node(conn, nodes, existing_node_ids, str(linked_event_id))
        if event_node_id in existing_node_ids and of_event_key not in seen_memory_edges:
            seen_memory_edges.add(of_event_key)
            edges.append(GraphEdge(source=memory_node_id, target=event_node_id, label="OF_EVENT"))

    # ── Personal Facts ────────────────────────────────────────────────────────
    fact_rows = _exec(conn, """
        MATCH (c:Character)-[:KNOWS_FACT]->(f:PersonalFact)
        RETURN c.id AS audience_id, f.id AS id, f.subject_id AS subject_id,
               f.category AS category, f.fact_text AS fact_text,
               f.normalized_key AS normalized_key, f.status AS status,
               f.valid_from AS valid_from, f.valid_until AS valid_until,
               f.confidence AS confidence, f.source AS source,
               f.created_at AS created_at, f.updated_at AS updated_at
        ORDER BY f.updated_at DESC
        LIMIT 40
    """)
    seen_facts: set[str] = set()
    seen_fact_edges: set[str] = set()
    for fact in fact_rows:
        fact_id = fact.get("id")
        aud_id = fact.get("audience_id")
        if not fact_id:
            continue
        if fact_id not in seen_facts:
            seen_facts.add(fact_id)
            nodes.append(GraphNode(
                id=f"personal_fact:{fact_id}",
                label=fact.get("normalized_key") or fact_id,
                type="personal_fact",
                subtitle=f"{fact.get('category') or ''} / {fact.get('status') or ''}",
                details={k: v for k, v in fact.items() if v not in (None, "", [], {}) and k != "audience_id"},
            ))
        knows_key = f"{aud_id}:{fact_id}:knows"
        if aud_id in char_by_id and knows_key not in seen_fact_edges:
            seen_fact_edges.add(knows_key)
            edges.append(GraphEdge(source=f"character:{aud_id}", target=f"personal_fact:{fact_id}", label="KNOWS_FACT"))
        subj_id = fact.get("subject_id")
        about_key = f"{fact_id}:{subj_id}:about"
        if subj_id in char_by_id and about_key not in seen_fact_edges:
            seen_fact_edges.add(about_key)
            edges.append(GraphEdge(source=f"personal_fact:{fact_id}", target=f"character:{subj_id}", label="ABOUT"))

    return GraphSnapshot(
        world_id=world_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        visible_time=current_time,
        committed_time=current_time,
        pending_time=None,
        time_source="db",
        nodes=nodes,
        edges=edges,
    )
