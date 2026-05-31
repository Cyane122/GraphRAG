# ================================
# src/simulation/systems/items/hints.py
#
# Fetch object-memory prompt hints for Items.
#
# Functions
#   - fetch_object_memory_hints(owner_id: str, pc_id: str, location_id: str, user_input: str, limit: int = 2) -> list[dict] : Fetch scene-relevant item-memory hints
#   - format_item_memory_hints(hints: list[ItemHint]) -> str : Build an item hint prompt block
#   - fetch_scoped_items(location_id: str, owner_id: str, pc_id: str) -> list[_ItemCandidate] : Fetch item candidates in scope
#   - fetch_character_location_id(char_id: str) -> str : Fetch a character location id
#   - looks_item_relevant(text: str) -> bool : Detect whether text may involve items
#   - mentions_candidate_item(text: str, candidates: list[_ItemCandidate]) -> bool : Detect whether text mentions a candidate item
# ================================
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from src.core.database import async_driver
from src.core.embedding.encoder import embed_async
from src.simulation.systems.items.actions import _ensure_item_schema
from src.simulation.systems.items.models import ItemHint, _ItemCandidate

_VECTOR_MAX_DISTANCE = 0.42
_WORD_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)
_ITEM_SIGNAL_RE = re.compile(
    r"item|object|gift|ring|necklace|bracelet|watch|photo|letter|key|phone|"
    r"bag|wallet|clothes|shirt|book|toy|lost|found|give|gave|take|took|"
    r"moved|placed|left|kept|held|picked|wore|wearing|removed",
    re.IGNORECASE,
)

async def fetch_object_memory_hints(
    owner_id: str,
    pc_id: str,
    location_id: str,
    user_input: str,
    limit: int = 2,
) -> list[dict]:
    """
    Fetch item-memory hints relevant to the current location and user input.

    The search combines scoped Item rows, lexical overlap against item text, and
    vector recall over Memory nodes that are connected by ANCHORS_MEMORY.
    """
    await _ensure_item_schema()
    scoped_items = await fetch_scoped_items(location_id, owner_id, pc_id)
    if not scoped_items:
        return []

    vector_hits = await _fetch_vector_item_memories(user_input, location_id, owner_id, pc_id)
    vector_scores = {
        hit["memory_id"]: max(0.0, 1.0 - float(hit.get("distance") or 1.0))
        for hit in vector_hits
        if hit.get("memory_id")
    }

    scored: list[ItemHint] = []
    for item in scoped_items:
        lexical = _score_item_relevance(item, user_input)
        memory_score = vector_scores.get(item.get("memory_id", ""), 0.0)
        location_bonus = 0.15 if item.get("location_id") == location_id else 0.0
        owner_bonus = 0.08 if item.get("owner_id") in {owner_id, pc_id} else 0.0
        weight_bonus = min(0.2, max(0, int(item.get("emotional_weight") or 0)) / 50)
        relevance = min(1.0, lexical + memory_score + location_bonus + owner_bonus + weight_bonus)

        if relevance <= 0.12 and not item.get("memory_summary"):
            continue
        scored.append(_build_hint(item, relevance))

    scored.sort(key=lambda h: (h.get("relevance", 0.0), h.get("importance", 0)), reverse=True)
    return [dict(hint) for hint in scored[: max(0, limit)]]


def format_item_memory_hints(hints: list[ItemHint]) -> str:
    """Render item-memory hints as a compact XML block for dynamic prompt."""
    if not hints:
        return ""

    lines: list[str] = []
    for hint in hints:
        item_name = hint.get("item_name") or hint.get("item_id", "item")
        memory = hint.get("memory_summary") or hint.get("description") or ""
        if memory:
            lines.append(f"- {item_name}: {memory}")

    return "<item_memory_hints>\n" + "\n".join(lines) + "\n</item_memory_hints>" if lines else ""




async def fetch_scoped_items(
    location_id: str,
    npc_id: str | None,
    pc_id: str | None,
) -> list[_ItemCandidate]:
    """Fetch Item rows near the current scene or owned by the active characters."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (i:Item)
            RETURN i.id               AS item_id,
                   i.name             AS item_name,
                   i.description      AS description,
                   i.owner_id         AS owner_id,
                   i.location_id      AS location_id,
                   i.visibility       AS visibility,
                   i.emotional_weight AS emotional_weight,
                   i.last_seen_at     AS last_seen_at
        """)
        item_rows = await rec.data()

    owner_ids = {value for value in (npc_id, pc_id) if value}
    items = [_normalize_item_row(row) for row in item_rows]
    scoped = [item for item in items if _in_scope(item, location_id, owner_ids)]

    anchored = await _fetch_anchor_memories([item["item_id"] for item in scoped])
    by_item = {row["item_id"]: row for row in anchored if row.get("item_id")}
    for item in scoped:
        memory = by_item.get(item["item_id"], {})
        item["memory_id"] = memory.get("memory_id", "")
        item["memory_summary"] = memory.get("memory_summary", "")
        item["importance"] = int(memory.get("importance") or 0)

    scoped.sort(
        key=lambda it: (
            it.get("location_id") == location_id,
            int(it.get("emotional_weight") or 0),
            int(it.get("importance") or 0),
        ),
        reverse=True,
    )
    return scoped


async def fetch_character_location_id(char_id: str) -> str:
    """Fetch the current location id for a character, falling back to GlobalState."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            RETURN d.location_id AS location_id
        """, char_id=char_id)
        row = await rec.single()
        if row and row.get("location_id"):
            return str(row["location_id"])

        rec = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentLocationId AS location_id
        """)
        row = await rec.single()
        if row and row.get("location_id"):
            return str(row["location_id"])
    return ""


async def _fetch_anchor_memories(item_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch the strongest Memory node anchored to each item id."""
    rows: list[dict[str, Any]] = []
    async with async_driver.session() as session:
        for item_id in item_ids:
            rec = await session.run("""
                MATCH (i:Item {id: $item_id})-[:ANCHORS_MEMORY]->(m:Memory)
                RETURN i.id         AS item_id,
                       m.id         AS memory_id,
                       m.summary    AS memory_summary,
                       m.importance AS importance
                ORDER BY m.importance DESC
                LIMIT 1
            """, item_id=item_id)
            rows.extend(await rec.data())
    return rows


async def _fetch_vector_item_memories(
    user_input: str,
    location_id: str,
    npc_id: str | None,
    pc_id: str | None,
) -> list[dict[str, Any]]:
    """Use the Memory vector index to find item-anchored memories relevant to the turn."""
    if not user_input.strip():
        return []

    try:
        query_embedding = await embed_async(user_input)
    except Exception as exc:
        print(f"[ItemMemory] query embedding failed: {exc}")
        return []

    async with async_driver.session() as session:
        rec = await session.run("""
            CALL QUERY_VECTOR_INDEX('Memory', 'memory_embeddings', $embedding, $candidates)
            WITH node AS mem, distance
            MATCH (i:Item)-[:ANCHORS_MEMORY]->(mem)
            WHERE distance <= $max_distance
            RETURN i.id               AS item_id,
                   i.owner_id         AS owner_id,
                   i.location_id      AS location_id,
                   mem.id             AS memory_id,
                   mem.summary        AS memory_summary,
                   mem.importance     AS importance,
                   distance
            ORDER BY distance ASC
            LIMIT $limit
        """,
            embedding=query_embedding,
            candidates=12,
            max_distance=_VECTOR_MAX_DISTANCE,
            limit=6,
        )
        rows = await rec.data()

    owner_ids = {value for value in (npc_id, pc_id) if value}
    return [
        dict(row)
        for row in rows
        if row.get("location_id") == location_id or row.get("owner_id") in owner_ids
    ]


def _normalize_item_row(row: dict[str, Any]) -> _ItemCandidate:
    """Normalize nullable Kuzu row values into predictable item fields."""
    return {
        "item_id": str(row.get("item_id") or ""),
        "item_name": str(row.get("item_name") or row.get("item_id") or ""),
        "description": str(row.get("description") or ""),
        "owner_id": str(row.get("owner_id") or ""),
        "location_id": str(row.get("location_id") or ""),
        "visibility": str(row.get("visibility") or ""),
        "emotional_weight": int(row.get("emotional_weight") or 0),
        "last_seen_at": str(row.get("last_seen_at") or ""),
    }


def _in_scope(item: _ItemCandidate, location_id: str, owner_ids: set[str]) -> bool:
    """Return True when an item is plausibly available to this scene."""
    if item.get("location_id") == location_id:
        return True
    if item.get("owner_id") in owner_ids:
        return True
    return item.get("visibility", "").lower() in {"global", "public", "always"}


def _score_item_relevance(item: _ItemCandidate, user_input: str) -> float:
    """Score lexical overlap between user input and item/memory text."""
    query_terms = set(_tokenize(user_input))
    if not query_terms:
        return 0.0

    item_text = " ".join(
        [item.get("item_name", ""), item.get("description", ""), item.get("memory_summary", "")]
    )
    item_terms = set(_tokenize(item_text))
    overlap = len(query_terms & item_terms)
    exact_name = item.get("item_name", "") and item.get("item_name", "") in user_input
    return min(0.45, overlap * 0.12) + (0.35 if exact_name else 0.0)


def _build_hint(item: _ItemCandidate, relevance: float) -> ItemHint:
    """Build a public ItemHint from a scored internal candidate."""
    memory = item.get("memory_summary") or item.get("description") or ""
    item_name = item.get("item_name") or item.get("item_id", "item")
    return {
        "item_id": item.get("item_id", ""),
        "item_name": item_name,
        "description": item.get("description", ""),
        "owner_id": item.get("owner_id", ""),
        "location_id": item.get("location_id", ""),
        "memory_id": item.get("memory_id", ""),
        "memory_summary": item.get("memory_summary", ""),
        "importance": int(item.get("importance") or item.get("emotional_weight") or 0),
        "relevance": round(relevance, 3),
        "hint": f"{item_name}: {memory}" if memory else item_name,
    }



def looks_item_relevant(text: str) -> bool:
    """Return True when text contains a generic object-update signal."""
    return bool(_ITEM_SIGNAL_RE.search(text))


def mentions_candidate_item(text: str, candidates: list[_ItemCandidate]) -> bool:
    """Return True when text names one of the candidate items."""
    for item in candidates:
        for field in ("item_id", "item_name"):
            value = item.get(field, "")
            if value and value in text:
                return True
    return False


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed-language text for rough lexical matching."""
    return [token.lower() for token in _WORD_RE.findall(text or "")]


def _safe_timestamp(timestamp: str) -> str:
    """Make a timestamp fragment safe for node ids."""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "", timestamp.replace("T", "_"))
    return cleaned[:32] or datetime.now().strftime("%Y%m%d_%H%M%S")


def _dedupe(values: list[str]) -> list[str]:
    """Return non-empty values while preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
