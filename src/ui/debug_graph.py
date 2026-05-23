# ================================
# src/ui/debug_graph.py
#
# Chainlit 개발용 그래프 스냅샷을 수집하고 별도 로컬 그래프 서버에 반영합니다.
#
# Functions
#   - build_debug_graph(pc_id: str, npc_id: str, world_id: str) -> GraphSnapshot : 현재 장면 중심 그래프 데이터를 생성
#   - send_debug_graph(pc_id: str, npc_id: str, world_id: str) -> None : 그래프 서버 URL을 안내
#   - upsert_debug_graph(pc_id: str, npc_id: str, world_id: str) -> None : 그래프 서버 스냅샷 갱신
# ================================

import json
from datetime import datetime
from typing import Any

import chainlit as cl

from src.core.database import async_driver
from src.ui.graph_models import GraphEdge, GraphNode, GraphSnapshot
from src.ui.graph_server import ensure_graph_server, update_graph_snapshot


def _clean(value: Any) -> Any:
    """Chainlit props로 넘길 수 있게 Kuzu 값을 작은 JSON 값으로 정리합니다."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean(item) for item in value]
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


def _compact_details(props: dict[str, Any], fields: tuple[str, ...] | None = None) -> dict[str, Any]:
    """빈 값을 제거하고 표시할 필드만 남깁니다."""
    keys = fields or tuple(props.keys())
    details: dict[str, Any] = {}
    for key in keys:
        value = _clean(props.get(key))
        if value in (None, "", [], {}):
            continue
        details[key] = value
    return details


def _node(
    node_id: str,
    label: str,
    node_type: str,
    subtitle: str = "",
    details: dict[str, Any] | None = None,
) -> GraphNode:
    """프론트엔드 그래프 노드 dict를 생성합니다."""
    return GraphNode(
        id=node_id,
        label=label or node_id,
        type=node_type,
        subtitle=subtitle,
        details=details or {},
    )


def _edge(source: str, target: str, label: str, details: dict[str, Any] | None = None) -> GraphEdge:
    """프론트엔드 그래프 엣지 dict를 생성합니다."""
    return GraphEdge(source=source, target=target, label=label, details=details or {})


async def _fetch_global_state() -> dict[str, Any]:
    """GlobalState 싱글톤의 주요 필드를 조회합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime AS currentTime,
                   gs.currentLocationId AS currentLocationId,
                   gs.weather AS weather,
                   gs.schedule_slot AS schedule_slot
            """
        )
        row = await result.single()
    return dict(row) if row else {}


async def _fetch_character_rows() -> list[dict[str, Any]]:
    """캐릭터, 위치, StaticProfile, DynamicInformation, DynamicState를 한 번에 조회합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character)
            OPTIONAL MATCH (c)-[:LOCATED_AT]->(l:Location)
            OPTIONAL MATCH (c)-[:HAS_STATE]->(d:DynamicState)
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            OPTIONAL MATCH (c)-[:HAS_INFO]->(di:DynamicInformation)
            RETURN c.id AS id,
                   c.name AS name,
                   c.type AS type,
                   l.id AS location_id,
                   l.name AS location_name,
                   d.location_id AS state_location_id,
                   d AS state,
                   sp AS static_profile,
                   di AS dynamic_info
            """
        )
        rows = await result.fetch_all()
    return [dict(row) for row in rows]


async def _fetch_relationship_rows() -> list[dict[str, Any]]:
    """Character 간 RELATIONSHIP 엣지를 조회합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (a:Character)-[r:RELATIONSHIP]->(b:Character)
            RETURN a.id AS source,
                   b.id AS target,
                   r.type AS type,
                   r.affinity AS affinity,
                   r.trust AS trust,
                   r.duration AS duration,
                   r.origin AS origin,
                   r.current_status AS current_status,
                   r.last_interaction AS last_interaction
            """
        )
        rows = await result.fetch_all()
    return [dict(row) for row in rows]


async def _fetch_location_rows(location_ids: set[str]) -> list[dict[str, Any]]:
    """화면에 포함할 Location 노드를 조회합니다."""
    if not location_ids:
        return []

    rows: list[dict[str, Any]] = []
    async with async_driver.session() as session:
        for location_id in sorted(location_ids):
            result = await session.run(
                """
                MATCH (l:Location {id: $location_id})
                OPTIONAL MATCH (c:Character)-[:LOCATED_AT]->(l)
                RETURN l.id AS id,
                       l.name AS name,
                       l.summary AS summary,
                       collect(c.id) AS current_chars
                """,
                location_id=location_id,
            )
            row = await result.single()
            if row:
                rows.append(dict(row))
    return rows


async def _fetch_personal_fact_rows(character_ids: set[str]) -> list[dict[str, Any]]:
    """Fetch PersonalFact nodes known by visible characters."""
    if not character_ids:
        return []

    rows: list[dict[str, Any]] = []
    async with async_driver.session() as session:
        for char_id in sorted(character_ids):
            result = await session.run(
                """
                MATCH (c:Character {id: $char_id})-[:KNOWS_FACT]->(f:PersonalFact)
                RETURN c.id AS audience_id,
                       f.id AS id,
                       f.subject_id AS subject_id,
                       f.audience_id AS stored_audience_id,
                       f.category AS category,
                       f.fact_text AS fact_text,
                       f.normalized_key AS normalized_key,
                       f.status AS status,
                       f.valid_from AS valid_from,
                       f.valid_until AS valid_until,
                       f.confidence AS confidence,
                       f.source AS source,
                       f.created_at AS created_at,
                       f.updated_at AS updated_at
                ORDER BY f.updated_at DESC
                LIMIT 20
                """,
                char_id=char_id,
            )
            rows.extend(dict(row) for row in await result.fetch_all())

    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        key = f"{row.get('audience_id')}:{row.get('id')}"
        if not row.get("id") or key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
    return unique_rows


async def _fetch_recent_events(character_ids: set[str], limit: int = 6) -> list[dict[str, Any]]:
    """포함된 캐릭터와 연결된 최근 Event를 조회합니다."""
    if not character_ids:
        return []

    rows: list[dict[str, Any]] = []
    async with async_driver.session() as session:
        for char_id in sorted(character_ids):
            result = await session.run(
                """
                MATCH (c:Character {id: $char_id})-[:INVOLVED_IN]->(e:Event)
                RETURN c.id AS char_id,
                       e.id AS id,
                       e.summary AS summary,
                       e.timestamp AS timestamp,
                       e.importance AS importance
                ORDER BY e.timestamp DESC
                LIMIT 6
                """,
                char_id=char_id,
            )
            rows.extend(dict(row) for row in await result.fetch_all())

    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        event_id = row.get("id")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        unique_rows.append(row)
    return unique_rows[:limit]


def _pending_time_state() -> dict[str, str | None]:
    """아직 DB에 커밋되지 않은 현재 턴 시간 계획을 읽습니다."""
    try:
        pending = cl.user_session.get("pending_commit") or {}
    except Exception:
        pending = {}
    manager_effects = pending.get("manager_effects") or {}
    time_plan = manager_effects.get("time_plan") or {}
    pending_time = time_plan.get("new_time")
    return {
        "pendingTime": pending_time,
        "timeSource": "pending" if pending_time else "committed",
    }


def _profile_node(
    char_id: str,
    char_name: str,
    node_id_prefix: str,
    node_type: str,
    profile: Any,
) -> GraphNode | None:
    """캐릭터 프로필 계열 노드를 그래프 노드로 변환합니다."""
    data = _clean(profile)
    if not isinstance(data, dict) or not data:
        return None
    label = f"{char_name} StaticInformation" if node_id_prefix == "static" else f"{char_name} DynamicInformation"
    subtitle = data.get("role") or data.get("grade_class") or data.get("nationality") or ""
    return _node(
        f"{node_id_prefix}:{char_id}",
        label,
        node_type,
        str(subtitle),
        _compact_details(data),
    )


async def build_debug_graph(pc_id: str, npc_id: str, world_id: str) -> GraphSnapshot:
    """현재 장면 중심 그래프 데이터를 생성합니다."""
    global_state = await _fetch_global_state()
    time_state = _pending_time_state()
    committed_time = global_state.get("currentTime")
    effective_time = time_state["pendingTime"] or committed_time
    char_rows = await _fetch_character_rows()
    char_by_id = {row["id"]: row for row in char_rows if row.get("id")}

    included_char_ids = set(char_by_id)
    included_location_ids = {
        row.get("location_id") or row.get("state_location_id")
        for row in char_rows
        if row.get("id") in included_char_ids and (row.get("location_id") or row.get("state_location_id"))
    }
    current_location_id = global_state.get("currentLocationId")
    if current_location_id:
        included_location_ids.add(current_location_id)

    location_rows = await _fetch_location_rows({str(x) for x in included_location_ids if x})
    relationship_rows = await _fetch_relationship_rows()
    event_rows = await _fetch_recent_events(included_char_ids)
    personal_fact_rows = await _fetch_personal_fact_rows(included_char_ids)

    nodes: list[GraphNode] = [
        _node(
            "global:singleton",
            "Global State",
            "global",
            str(effective_time or ""),
            {
                **_compact_details(global_state),
                "visibleTime": effective_time,
                "committedTime": committed_time,
                "pendingTime": time_state["pendingTime"],
                "timeSource": time_state["timeSource"],
            },
        )
    ]
    edges: list[GraphEdge] = []

    for location in location_rows:
        location_node_id = f"location:{location['id']}"
        nodes.append(
            _node(
                location_node_id,
                location.get("name") or location["id"],
                "location",
                location["id"],
                _compact_details(location),
            )
        )
        if location["id"] == current_location_id:
            edges.append(_edge("global:singleton", location_node_id, "current"))

    for char_id in sorted(included_char_ids):
        row = char_by_id[char_id]
        char_node_id = f"character:{char_id}"
        role = "PC" if char_id == pc_id else "NPC" if char_id == npc_id else row.get("type") or "Character"
        nodes.append(
            _node(
                char_node_id,
                row.get("name") or char_id,
                "character",
                role,
                {
                    "id": char_id,
                    "type": row.get("type"),
                    "location": row.get("location_name") or row.get("location_id") or row.get("state_location_id"),
                    "location_source": "LOCATED_AT" if row.get("location_id") else "DynamicState.location_id",
                },
            )
        )

        location_id = row.get("location_id") or row.get("state_location_id")
        if location_id:
            edges.append(_edge(char_node_id, f"location:{location_id}", "LOCATED_AT"))

        state = _clean(row.get("state"))
        if isinstance(state, dict):
            state_node_id = f"state:{char_id}"
            state_subtitle = (
                state.get("physical_condition")
                or state.get("mood")
                or state.get("mental_condition")
                or ""
            )
            nodes.append(
                _node(
                    state_node_id,
                    f"{row.get('name') or char_id} state",
                    "state",
                    str(state_subtitle),
                    _compact_details(state),
                )
            )
            edges.append(_edge(char_node_id, state_node_id, "HAS_STATE"))

        static_node = _profile_node(
            char_id,
            row.get("name") or char_id,
            "static",
            "static_information",
            row.get("static_profile"),
        )
        if static_node:
            nodes.append(static_node)
            edges.append(_edge(char_node_id, static_node.id, "HAS_PROFILE"))

        info_node = _profile_node(
            char_id,
            row.get("name") or char_id,
            "info",
            "dynamic_information",
            row.get("dynamic_info"),
        )
        if info_node:
            nodes.append(info_node)
            edges.append(_edge(char_node_id, info_node.id, "HAS_INFO"))

    for rel in relationship_rows:
        source = rel.get("source")
        target = rel.get("target")
        if source not in included_char_ids or target not in included_char_ids:
            continue
        details = _compact_details(rel)
        label_bits = ["REL"]
        if rel.get("affinity") is not None:
            label_bits.append(f"aff {rel['affinity']}")
        if rel.get("trust") is not None:
            label_bits.append(f"trust {rel['trust']}")
        edges.append(_edge(f"character:{source}", f"character:{target}", " / ".join(label_bits), details))

    for event in event_rows:
        event_node_id = f"event:{event['id']}"
        nodes.append(
            _node(
                event_node_id,
                event.get("summary") or event["id"],
                "event",
                event.get("timestamp") or "",
                _compact_details(event),
            )
        )
        char_id = event.get("char_id")
        if char_id in included_char_ids:
            edges.append(_edge(f"character:{char_id}", event_node_id, "INVOLVED_IN"))

    for fact in personal_fact_rows:
        fact_node_id = f"personal_fact:{fact['id']}"
        category = fact.get("category") or "misc"
        status = fact.get("status") or ""
        nodes.append(
            _node(
                fact_node_id,
                fact.get("normalized_key") or fact["id"],
                "personal_fact",
                f"{category} / {status}",
                _compact_details(fact),
            )
        )
        audience_id = fact.get("audience_id")
        if audience_id in included_char_ids:
            edges.append(_edge(f"character:{audience_id}", fact_node_id, "KNOWS_FACT"))
        subject_id = fact.get("subject_id")
        if subject_id in included_char_ids:
            edges.append(_edge(fact_node_id, f"character:{subject_id}", "ABOUT"))

    try:
        _thread_id = cl.context.session.thread_id
    except Exception:
        _thread_id = ""

    return GraphSnapshot(
        thread_id=_thread_id,
        world_id=world_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        visible_time=effective_time,
        committed_time=committed_time,
        pending_time=time_state["pendingTime"],
        time_source=time_state["timeSource"] or "none",
        nodes=nodes,
        edges=edges,
    )


async def send_debug_graph(pc_id: str, npc_id: str, world_id: str) -> None:
    """그래프 서버 URL과 현재 스냅샷 상태를 보냅니다."""
    graph = await build_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
    url = ensure_graph_server()
    update_graph_snapshot(graph)
    await cl.Message(
        content=(
            f"그래프 관찰 창: {url}\n"
            f"- nodes: `{len(graph.nodes)}` / edges: `{len(graph.edges)}`\n"
            f"- visible time: `{graph.visible_time or 'unknown'}` "
            f"({graph.time_source})\n"
            f"- snapshot: `{graph.generated_at}`"
        ),
        author="그래프",
    ).send()


async def upsert_debug_graph(pc_id: str, npc_id: str, world_id: str) -> None:
    """최신 그래프 데이터를 별도 그래프 서버에 반영합니다."""
    graph = await build_debug_graph(pc_id=pc_id, npc_id=npc_id, world_id=world_id)
    ensure_graph_server()
    update_graph_snapshot(graph)
