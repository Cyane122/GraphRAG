# ================================
# src/simulation/systems/items/__init__.py
#
# Public API for the Item system and practical item updates.
#
# Classes
#   - ItemHint : Prompt-ready item memory hint
#   - ItemUpdateResult : Item update result
#
# Functions
#   - fetch_object_memory_hints(owner_id: str, pc_id: str, location_id: str, user_input: str, limit: int = 2) -> list[dict] : Fetch scene-relevant item-memory hints
#   - format_item_memory_hints(hints: list[ItemHint]) -> str : Build an item hint prompt block
#   - ensure_item_memory(item_id: str, char_ids: list[str], summary: str, importance: int, timestamp: str, event_id: str | None = None) -> str | None : Create a Memory linked to an Item
#   - apply_item_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply item updates
# ================================
from __future__ import annotations

from datetime import datetime

from src.simulation.systems.items.actions import (
    _apply_item_action,
    _ensure_item_schema,
    _generate_item_actions,
    ensure_item_memory,
)
from src.simulation.systems.items.hints import (
    fetch_character_location_id,
    fetch_object_memory_hints,
    fetch_scoped_items,
    format_item_memory_hints,
    looks_item_relevant,
    mentions_candidate_item,
)
from src.simulation.systems.items.models import ItemHint, ItemUpdateResult, _ItemAction, _ItemCandidate

_MAX_CANDIDATE_ITEMS = 12
_MAX_UPDATE_ACTIONS = 4

async def apply_item_updates(
    actor_response: str,
    owner_id: str,
    pc_id: str,
    current_time: datetime,
    event_id: str | None = None,
) -> None:
    """
    Infer and apply practical Item updates after an actor response.

    This function only mutates existing Item nodes. It can move an item, change
    owner_id, mark it lost, append a short description note, or create an
    anchored item memory when the object gained narrative weight.
    """
    await _ensure_item_schema()
    scene_text = actor_response.strip()
    location_id = await fetch_character_location_id(owner_id)
    candidates = (await fetch_scoped_items(location_id, owner_id, pc_id))[:_MAX_CANDIDATE_ITEMS]
    if not candidates:
        return
    if not looks_item_relevant(scene_text) and not mentions_candidate_item(scene_text, candidates):
        return

    actions = await _generate_item_actions(scene_text, candidates, owner_id, pc_id, location_id)
    for action in actions[:_MAX_UPDATE_ACTIONS]:
        await _apply_item_action(
            action=action,
            valid_item_ids={item["item_id"] for item in candidates},
            npc_id=owner_id,
            pc_id=pc_id,
            location_id=location_id,
            timestamp=current_time.isoformat(),
            event_id=event_id,
        )
