# ================================
# src/simulation/systems/items/actions.py
#
# Create item memories and apply LLM-generated item actions.
#
# Functions
#   - ensure_item_memory(item_id: str, char_ids: list[str], summary: str, importance: int, timestamp: str, event_id: str | None) -> str | None : Create a Memory linked to an Item; OF_EVENT edge created when event_id is provided
#   - _generate_item_actions(scene_text: str, candidates: list[_ItemCandidate], npc_id: str, pc_id: str, location_id: str) -> list[_ItemAction] : Generate item action candidates
#   - _apply_item_action(action: _ItemAction, valid_item_ids: set[str], npc_id: str, pc_id: str, location_id: str, timestamp: str, event_id: str | None) -> ItemUpdateResult : Apply an item action
# ================================
import json
import re
from typing import Any

from src.config import MODEL_COMPLEX_UPDATER as ITEM_MODEL
from src.core.database import async_driver
from src.core.embedding.encoder import embed_async
from src.core.llm.client import get_model, extract_json_from_llm
from src.simulation.systems.items.models import ItemUpdateResult, _ItemAction, _ItemCandidate

_MEMORY_MIN_IMPORTANCE = 3

async def ensure_item_memory(
    item_id: str,
    char_ids: list[str],
    summary: str,
    importance: int,
    timestamp: str,
    event_id: str | None = None,
) -> str | None:
    """
    Create a Memory node anchored to an Item and remembered by the given characters.

    Returns the created memory id, or None when the input is too weak to store.
    """
    await _ensure_item_schema()
    summary = summary.strip()
    if not item_id or not summary or importance < _MEMORY_MIN_IMPORTANCE:
        return None

    safe_ts = _safe_timestamp(timestamp)
    mem_id = f"mem_item_{item_id}_{safe_ts}"
    memory_event_id = event_id or f"item_{item_id}_{safe_ts}"

    embedding = None
    try:
        embedding = await embed_async(summary)
    except Exception as exc:
        print(f"[ItemMemory] embedding failed, storing text only: {exc}")

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (m:Memory {id: $mid}) RETURN m.id AS id",
            mid=mem_id,
        )
        if await rec.single():
            return mem_id

        await session.run("""
            CREATE (m:Memory {
                id:               $mid,
                event_id:         $event_id,
                char_id:          $char_id,
                summary:          $summary,
                embedding:        $embedding,
                importance:       $importance,
                distortion_level: 0.0,
                summary_level:    0,
                created_at:       $timestamp,
                last_decayed_at:  $timestamp
            })
        """,
            mid=mem_id,
            event_id=memory_event_id,
            char_id=",".join(_dedupe(char_ids)),
            summary=summary,
            embedding=embedding,
            importance=importance,
            timestamp=timestamp,
        )

        await session.run("""
            MATCH (i:Item {id: $item_id}), (m:Memory {id: $mid})
            CREATE (i)-[:ANCHORS_MEMORY]->(m)
        """, item_id=item_id, mid=mem_id)

        for char_id in _dedupe(char_ids):
            await session.run("""
                MATCH (c:Character {id: $char_id}), (m:Memory {id: $mid})
                CREATE (c)-[:REMEMBERS]->(m)
            """, char_id=char_id, mid=mem_id)

        # OF_EVENT 없이 생성된 메모리는 distortion.py의 MATCH 조건을 통과하지 못하므로
        # 실제 Event 노드가 있을 때만 엣지를 생성한다.
        if event_id:
            await session.run("""
                MATCH (m:Memory {id: $mid}), (e:Event {id: $eid})
                CREATE (m)-[:OF_EVENT]->(e)
            """, mid=mem_id, eid=event_id)

    print(f"[ItemMemory] anchored memory created: {item_id} -> {mem_id}")
    return mem_id

async def _ensure_item_schema() -> None:
    """Create Item-related tables and best-effort columns for older Kuzu DBs."""
    async with async_driver.session() as session:
        ddl_statements = [
            """CREATE NODE TABLE IF NOT EXISTS Item(
                id STRING,
                name STRING,
                description STRING,
                owner_id STRING,
                location_id STRING,
                emotional_weight INT64,
                visibility STRING,
                last_seen_at STRING,
                PRIMARY KEY(id)
            )""",
            "CREATE REL TABLE IF NOT EXISTS OWNS(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS GAVE(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS ANCHORS_MEMORY(FROM Item TO Memory)",
        ]
        alter_statements = [
            "ALTER TABLE Item ADD location_id STRING DEFAULT ''",
            "ALTER TABLE Item ADD emotional_weight INT64 DEFAULT 0",
            "ALTER TABLE Item ADD visibility STRING DEFAULT ''",
            "ALTER TABLE Item ADD last_seen_at STRING DEFAULT ''",
        ]
        for ddl in ddl_statements + alter_statements:
            try:
                await session.run(ddl)
            except Exception:
                pass

async def _generate_item_actions(
    scene_text: str,
    candidates: list[_ItemCandidate],
    npc_id: str,
    pc_id: str,
    location_id: str,
) -> list[_ItemAction]:
    """Ask the state updater model for conservative item mutations."""
    payload = [
        {
            "item_id": item["item_id"],
            "name": item.get("item_name", ""),
            "description": item.get("description", ""),
            "owner_id": item.get("owner_id", ""),
            "location_id": item.get("location_id", ""),
            "emotional_weight": item.get("emotional_weight", 0),
            "memory": item.get("memory_summary", ""),
        }
        for item in candidates
    ]

    prompt = f"""Update Item nodes only when explicitly changed or given new narrative weight.
npc={npc_id} | pc={pc_id} | loc={location_id}

Actions: anchor_memory (new association) / move (explicit relocation) / transfer_owner (given/taken) / mark_lost / update_description (new physical detail)

Rules: item_id from candidates only. No new items/locations. importance 0-10; 3+ for stored memory. memory_summary: 1 short Korean sentence. Return [] if nothing changed.

Candidates:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Scene:
{scene_text[:2400]}

Return ONLY JSON array:
[
  {{
    "item_id": "existing_id",
    "action": "anchor_memory",
    "memory_summary": "short Korean sentence",
    "importance": 3,
    "new_location_id": null,
    "new_owner_id": null,
    "description_append": null
  }}
]"""

    try:
        model = get_model(
            ITEM_MODEL,
            system_prompt="Conservative item state updater. Prefer no update over speculative.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 1024,
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
        )
        parsed = extract_json_from_llm(resp.text, source="item_updates")
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except Exception as exc:
        print(f"[ItemMemory] update plan failed: {exc}")
    return []


async def _apply_item_action(
    action: _ItemAction,
    valid_item_ids: set[str],
    npc_id: str,
    pc_id: str,
    location_id: str,
    timestamp: str,
    event_id: str | None,
) -> ItemUpdateResult:
    """Apply one validated item action to the graph."""
    item_id = str(action.get("item_id") or "")
    action_name = str(action.get("action") or "")
    if item_id not in valid_item_ids:
        return {"item_id": item_id, "action": action_name, "applied": False, "reason": "unknown item_id"}

    if action_name == "anchor_memory":
        memory_id = await ensure_item_memory(
            item_id=item_id,
            char_ids=[npc_id, pc_id],
            summary=str(action.get("memory_summary") or ""),
            importance=int(action.get("importance") or 0),
            timestamp=timestamp,
            event_id=event_id,
        )
        return {
            "item_id": item_id,
            "action": action_name,
            "applied": bool(memory_id),
            "reason": "stored" if memory_id else "memory skipped",
            "memory_id": memory_id or "",
        }

    if action_name == "move":
        await _update_item_fields(
            item_id,
            {"location_id": action.get("new_location_id") or location_id, "last_seen_at": timestamp},
        )
        return {"item_id": item_id, "action": action_name, "applied": True, "reason": "location updated"}

    if action_name == "transfer_owner":
        new_owner_id = action.get("new_owner_id")
        if new_owner_id not in {npc_id, pc_id}:
            return {"item_id": item_id, "action": action_name, "applied": False, "reason": "invalid owner"}
        await _update_item_fields(
            item_id,
            {"owner_id": new_owner_id, "location_id": location_id, "last_seen_at": timestamp},
        )
        return {"item_id": item_id, "action": action_name, "applied": True, "reason": "owner updated"}

    if action_name == "mark_lost":
        await _update_item_fields(
            item_id,
            {"location_id": "unknown", "visibility": "lost", "last_seen_at": timestamp},
        )
        return {"item_id": item_id, "action": action_name, "applied": True, "reason": "marked lost"}

    if action_name == "update_description":
        append_text = str(action.get("description_append") or "").strip()
        if not append_text:
            return {"item_id": item_id, "action": action_name, "applied": False, "reason": "empty description"}
        await _append_item_description(item_id, append_text, timestamp)
        return {"item_id": item_id, "action": action_name, "applied": True, "reason": "description updated"}

    return {"item_id": item_id, "action": action_name, "applied": False, "reason": "unsupported action"}


async def _update_item_fields(item_id: str, fields: dict[str, Any]) -> None:
    """Update a small set of trusted Item fields."""
    allowed = {"owner_id", "location_id", "visibility", "last_seen_at"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return

    set_clause = ", ".join(f"i.{key} = ${key}" for key in updates)
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (i:Item {{id: $item_id}}) SET {set_clause}",
            item_id=item_id,
            **updates,
        )


async def _append_item_description(item_id: str, append_text: str, timestamp: str) -> None:
    """Append one concise note to an item's description and refresh last_seen_at."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (i:Item {id: $item_id}) RETURN i.description AS description",
            item_id=item_id,
        )
        row = await rec.single()
        current = row["description"] if row and row.get("description") else ""
        separator = " " if current else ""
        new_description = f"{current}{separator}{append_text}"[:1000]

        await session.run("""
            MATCH (i:Item {id: $item_id})
            SET i.description = $description,
                i.last_seen_at = $timestamp
        """, item_id=item_id, description=new_description, timestamp=timestamp)


def _safe_timestamp(timestamp: str) -> str:
    """Timestamp 문자열을 id-safe suffix로 변환합니다."""
    return re.sub(r"[^0-9A-Za-z]+", "", str(timestamp))[:32] or "unknown"


def _dedupe(values: list[str]) -> list[str]:
    """빈 값을 제외하고 순서를 보존하며 중복을 제거합니다."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
