# ================================
# src/ui/web_app/analysis_tools.py
#
# Standalone web UI database-backed diagnostic tool rendering.
#
# Functions
#   - render_database_tool(tool_name: str, state: ConversationState) -> str : Render a read-only database tool result.
# ================================

from __future__ import annotations

import json
from typing import Any

from src.core.database import async_driver
from src.ui.web_app.models import ConversationState
from src.ui.web_app.runtime import ActiveConversation, initialize_conversation, snapshot_game_time

_TOOL_TITLES = {
    "relationships": "관계망 분석",
    "ooc-smoke": "OOC 테스트",
    "conflicts": "설정 충돌 점검",
}


def _clean(value: Any) -> Any:
    """Return a compact JSON-safe value for display-oriented analysis."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_clean(item) for item in value]
    try:
        data = dict(value)
        if isinstance(data.get("props"), str):
            try:
                props = json.loads(data["props"])
                if isinstance(props, dict):
                    data = {key: raw for key, raw in data.items() if key != "props"}
                    data.update(props)
            except json.JSONDecodeError:
                pass
        return {key: _clean(raw) for key, raw in data.items()}
    except (TypeError, ValueError):
        return str(value)


def _text(value: Any, fallback: str = "") -> str:
    """Convert nullable query values into stripped display text."""
    if value in (None, "", [], {}):
        return fallback
    return str(value).strip()


def _number(value: Any, fallback: int = 0) -> int:
    """Convert nullable query values into an integer score."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


async def _query_data(query: str, **params: Any) -> list[dict[str, Any]]:
    """Run a Kuzu query and return dict rows."""
    async with async_driver.session() as session:
        result = await session.run(query, **params)
        rows = await result.fetch_all()
    return [dict(row) for row in rows]


async def _fetch_character_rows() -> list[dict[str, Any]]:
    """Fetch character state rows used by the diagnostic tools."""
    return await _query_data(
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
               d.mood AS mood,
               d.stress_level AS stress_level,
               d.emotional_state AS emotional_state,
               sp AS static_profile,
               di AS dynamic_info
        """
    )


async def _fetch_relationship_rows() -> list[dict[str, Any]]:
    """Fetch directed character relationship rows."""
    return await _query_data(
        """
        MATCH (a:Character)-[r:RELATIONSHIP]->(b:Character)
        RETURN a.id AS source,
               a.name AS source_name,
               b.id AS target,
               b.name AS target_name,
               r.type AS type,
               r.affinity AS affinity,
               r.trust AS trust,
               r.current_status AS current_status,
               r.summary AS summary,
               r.last_interaction AS last_interaction
        """
    )


def _character_name_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Build id-to-display-name lookup from character rows."""
    return {str(row["id"]): _text(row.get("name"), str(row["id"])) for row in rows if row.get("id")}


def _render_state_lines(rows: list[dict[str, Any]], pc_id: str, npc_id: str) -> list[str]:
    """Render compact current-state lines for the active PC and NPC."""
    selected = [row for row in rows if row.get("id") in {pc_id, npc_id}]
    lines: list[str] = []
    for row in selected:
        location = _text(row.get("location_name")) or _text(row.get("state_location_id")) or "위치 미상"
        mood = _text(row.get("mood"), "mood 없음")
        stress = _text(row.get("stress_level"), "stress 없음")
        task = _text(row.get("emotional_state"), "현재 상태 기록 없음")
        lines.append(f"- {row.get('name') or row.get('id')} `{row.get('id')}`: {location}, {mood}, stress {stress}, {task}")
    return lines or ["- 활성 PC/NPC 상태를 찾지 못했습니다."]


def _render_relationships(rows: list[dict[str, Any]], characters: list[dict[str, Any]], state: ConversationState) -> str:
    """Render relationship analysis from relationship and character rows."""
    names = _character_name_map(characters)
    sorted_rows = sorted(
        rows,
        key=lambda row: abs(_number(row.get("affinity"))) + abs(_number(row.get("trust"))),
        reverse=True,
    )
    spotlight = [
        row for row in sorted_rows
        if row.get("source") in {state.pc_id, state.npc_id} or row.get("target") in {state.pc_id, state.npc_id}
    ][:8]
    top_rows = spotlight or sorted_rows[:8]
    relationship_lines = []
    for row in top_rows:
        source = names.get(str(row.get("source")), _text(row.get("source"), "?"))
        target = names.get(str(row.get("target")), _text(row.get("target"), "?"))
        affinity = _text(row.get("affinity"), "0")
        trust = _text(row.get("trust"), "0")
        status = _text(row.get("current_status") or row.get("summary"), "상태 기록 없음")
        relationship_lines.append(f"- {source} -> {target}: affinity {affinity}, trust {trust}. {status}")

    state_lines = _render_state_lines(characters, state.pc_id, state.npc_id)
    return "\n".join(
        [
            "## 관계망 분석",
            "",
            f"- thread: `{state.thread_id}`",
            f"- world/scenario: `{state.world_id}` / `{state.scenario_id or 'default'}`",
            f"- characters: {len(characters)}명, relationships: {len(rows)}개",
            "",
            "### 현재 장면 중심 상태",
            *state_lines,
            "",
            "### 주요 관계",
            *(relationship_lines or ["- 기록된 RELATIONSHIP 엣지가 없습니다."]),
        ]
    )


async def _render_ooc_smoke(characters: list[dict[str, Any]], relationships: list[dict[str, Any]], state: ConversationState) -> str:
    """Render a read-only OOC/database connection smoke result."""
    game_time = await snapshot_game_time()
    pc_present = any(row.get("id") == state.pc_id for row in characters)
    npc_present = any(row.get("id") == state.npc_id for row in characters)
    return "\n".join(
        [
            "## OOC 테스트",
            "",
            "DB 연결과 현재 스레드 컨텍스트를 읽기 전용으로 확인했습니다.",
            "",
            f"- thread: `{state.thread_id}`",
            f"- world/scenario: `{state.world_id}` / `{state.scenario_id or 'default'}`",
            f"- pc_id: `{state.pc_id}` ({'확인됨' if pc_present else 'DB에서 찾지 못함'})",
            f"- npc_id: `{state.npc_id}` ({'확인됨' if npc_present else 'DB에서 찾지 못함'})",
            f"- currentTime: `{game_time or '없음'}`",
            f"- character rows: {len(characters)}",
            f"- relationship rows: {len(relationships)}",
            "",
            "OOC patch는 실행하지 않았고, graph mutation도 만들지 않았습니다.",
        ]
    )


def _profile_dict(value: Any) -> dict[str, Any]:
    """Convert a profile node into a plain dict when possible."""
    cleaned = _clean(value)
    return cleaned if isinstance(cleaned, dict) else {}


def _render_conflicts(characters: list[dict[str, Any]], state: ConversationState) -> str:
    """Render likely configuration conflicts from static and dynamic rows."""
    issues: list[str] = []
    for row in characters:
        char_id = _text(row.get("id"), "?")
        name = _text(row.get("name"), char_id)
        static_profile = _profile_dict(row.get("static_profile"))
        dynamic_info = _profile_dict(row.get("dynamic_info"))
        location_id = _text(row.get("location_id"))
        state_location_id = _text(row.get("state_location_id"))

        if not static_profile:
            issues.append(f"- {name} `{char_id}`: StaticProfile 연결이 없습니다.")
        if not dynamic_info:
            issues.append(f"- {name} `{char_id}`: DynamicInformation 연결이 없습니다.")
        if location_id and state_location_id and location_id != state_location_id:
            issues.append(f"- {name} `{char_id}`: LOCATED_AT `{location_id}`와 DynamicState.location_id `{state_location_id}`가 다릅니다.")
        if not _text(row.get("mood")) and not _text(row.get("emotional_state")):
            issues.append(f"- {name} `{char_id}`: 현재 감정/mood 기록이 비어 있습니다.")

        static_age = _number(static_profile.get("age"), -1)
        dynamic_age = _number(dynamic_info.get("age"), -1)
        if static_age >= 0 and dynamic_age >= 0 and static_age != dynamic_age:
            issues.append(f"- {name} `{char_id}`: StaticProfile.age {static_age}와 DynamicInformation.age {dynamic_age}가 다릅니다.")

    active_ids = {state.pc_id, state.npc_id}
    missing_active = [char_id for char_id in active_ids if char_id and not any(row.get("id") == char_id for row in characters)]
    for char_id in missing_active:
        issues.insert(0, f"- 활성 캐릭터 `{char_id}`를 Character 테이블에서 찾지 못했습니다.")

    return "\n".join(
        [
            "## 설정 충돌 점검",
            "",
            f"- thread: `{state.thread_id}`",
            f"- world/scenario: `{state.world_id}` / `{state.scenario_id or 'default'}`",
            f"- checked characters: {len(characters)}",
            "",
            "### 점검 결과",
            *(issues[:20] or ["- 명확한 설정 충돌 후보를 찾지 못했습니다."]),
        ]
    )


async def render_database_tool(tool_name: str, state: ConversationState) -> str:
    """Render a read-only database tool result."""
    if tool_name not in _TOOL_TITLES:
        raise KeyError(f"unknown tool: {tool_name}")
    if not state.pc_id or not state.npc_id:
        initialize_conversation(state)

    async with ActiveConversation(state):
        characters = await _fetch_character_rows()
        relationships = await _fetch_relationship_rows()
        if tool_name == "relationships":
            return _render_relationships(relationships, characters, state)
        if tool_name == "ooc-smoke":
            return await _render_ooc_smoke(characters, relationships, state)
        return _render_conflicts(characters, state)
