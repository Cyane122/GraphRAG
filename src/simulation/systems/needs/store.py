# ================================
# src/simulation/systems/needs/store.py
#
# Read and write NeedsState and related profile records.
#
# Functions
#   - _apply_safety_decay(npc_id: str, old_safety: float, elapsed_min: float, current_time: datetime) -> float : Apply safety decay
#   - _fetch_all_npcs(exclude_id: str) -> list[dict] : Fetch all NPCs excluding the PC (includes aliases)
#   - _fetch_needs(npc_id: str) -> dict : Fetch NPC need state
#   - _fetch_profile_props(npc_id: str) -> dict : Fetch StaticProfile props
#   - _write_needs(npc_id: str, updates: dict) -> None : Update NeedsState
# ================================
import json
from datetime import datetime

from src.agents.resolver import NEED_DEFAULTS
from src.core.database import async_driver
from src.simulation.systems.needs.math import NEED_BASE_RATES

async def _apply_safety_decay(
    npc_id:      str,
    old_safety:  float,
    elapsed_min: float,
    current_time: datetime,
) -> float:
    """
    미해소 Event의 safety_impact × decay_rate 합산으로 Safety 재계산.
    Safety = base(0.05) + Σ[ impact × max(0, 1 - decay_rate × elapsed) ]
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event)
            WHERE e.safety_impact > 0 AND e.safety_resolved = false
            RETURN e.safety_impact     AS impact,
                   e.safety_decay_rate AS decay_rate,
                   e.timestamp         AS timestamp
        """, cid=npc_id)
        rows = await rec.data()

    total = 0.05
    for row in rows:
        impact     = row["impact"] or 0.0
        decay_rate = row["decay_rate"] or 0.002
        age_min    = _event_age_minutes(row.get("timestamp"), current_time, elapsed_min)
        residual   = max(0.0, 1.0 - decay_rate * age_min)
        total     += impact * residual

    return round(min(1.0, total), 4)


def _event_age_minutes(timestamp: object, current_time: datetime, default: float) -> float:
    """Event timestamp가 ISO일 때 누적 경과 분을 쓰고, 아니면 현재 턴 경과로 대체한다."""
    if not isinstance(timestamp, str):
        return max(0.0, default)
    try:
        event_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return max(0.0, default)
    return max(0.0, (current_time - event_time).total_seconds() / 60)


# ════════════════════════════════════════════════════════════
# Libido hint 생성
# ════════════════════════════════════════════════════════════


async def _fetch_all_characters(exclude_id: str | None = None) -> list[dict]:
    """모든 Character 노드를 반환하며, 필요하면 특정 id를 제외합니다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            WHERE $exclude IS NULL OR c.id <> $exclude
            RETURN c.id AS id, c.name AS name, c.type AS type
        """, exclude=exclude_id)
        rows = await rec.data()
    return [
        {
            "id": r["id"],
            "name": r.get("name") or r["id"],
            "type": r.get("type") or "",
        }
        for r in rows
        if r["id"]
    ]


async def _fetch_all_npcs(exclude_id: str) -> list[dict]:
    """모든 NPC (PC 제외) 반환. aliases 포함."""
    async with async_driver.session() as session:
        # libido_excluded는 StaticProfile 스키마에 정의되지 않아 Kuzu Binder 오류 발생
        # → 필터 없이 모든 NPC 반환 (PC 제외)
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.id <> $exclude
            RETURN c.id AS id, c.name AS name, c.type AS type, c.aliases AS aliases
        """, exclude=exclude_id)
        rows = await rec.data()
    return [
        {
            "id": r["id"],
            "name": r.get("name") or r["id"],
            "type": r.get("type") or "",
            "aliases": r.get("aliases") or [],
        }
        for r in rows
        if r["id"]
    ]


async def _fetch_needs(npc_id: str) -> dict:
    """
    NPC의 현재 욕구 수치 딕셔너리 반환.
    DynamicState → NeedsState 순으로 탐색.
    없으면 NeedsState 노드 자동 생성.
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d AS props
        """, cid=npc_id)
        row = await rec.single()
        dynamic_props = dict(row["props"]) if row and row["props"] else {}

        rec2 = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN n AS props
        """, cid=npc_id)
        row2 = await rec2.single()
        needs_props = {f: v for f, v in NEED_DEFAULTS.items()}
        if row2 and row2["props"]:
            needs_props.update(dict(row2["props"]))
            needs_props.update(dynamic_props)
            return needs_props

        defaults       = dict(needs_props)
        defaults["id"] = f"{npc_id}_needs"
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_NEEDS]->(n:NeedsState {
                id:     $id,
                hunger: $hunger,
                rest:   $rest,
                social: $social,
                fun:    $fun,
                safety: $safety,
                libido: $libido
            })
        """, cid=npc_id, **defaults)
        defaults.update(dynamic_props)
        return defaults


async def _fetch_profile_props(npc_id: str) -> dict:
    """StaticProfile 속성 반환 (sexual_tendency, libido_* 등)."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props_json
        """, cid=npc_id)
        row = await rec.single()
        if not row or not row["props_json"]:
            return {}
        raw = row["props_json"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return {}
        return raw if isinstance(raw, dict) else {}


async def _write_needs(npc_id: str, updates: dict) -> None:
    """욕구 수치를 NeedsState에 저장."""
    if not updates:
        return

    need_keys    = set(NEED_BASE_RATES.keys())
    need_updates = {k: v for k, v in updates.items() if k in need_keys}
    if not need_updates:
        return

    set_clause = ", ".join(f"n.{k} = ${k}" for k in need_updates)
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN n.id AS nid
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            defaults = {f: need_updates.get(f, v) for f, v in NEED_DEFAULTS.items()}
            defaults["id"] = f"{npc_id}_needs"
            await session.run("""
                MATCH (c:Character {id: $cid})
                CREATE (c)-[:HAS_NEEDS]->(n:NeedsState {
                    id:     $id,
                    hunger: $hunger,
                    rest:   $rest,
                    social: $social,
                    fun:    $fun,
                    safety: $safety,
                    libido: $libido
                })
            """, cid=npc_id, **defaults)
            return

        await session.run(
            f"MATCH (c:Character {{id: $cid}})-[:HAS_NEEDS]->(n:NeedsState) SET {set_clause}",
            cid=npc_id, **need_updates,
        )
