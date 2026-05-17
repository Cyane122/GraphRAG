# ================================
# src/simulation/systems/items/models.py
#
# Shared TypedDict models for the Item memory/update system.
#
# Classes
#   - ItemHint : Prompt-ready item memory hint
#   - ItemUpdateResult : Item update result
#   - _ItemCandidate : Candidate item query record
#   - _ItemAction : Item action returned by the LLM
# ================================
from typing import Literal, TypedDict

class ItemHint(TypedDict, total=False):
    """Prompt-facing item hint with the item and its strongest anchored memory."""

    item_id: str
    item_name: str
    description: str
    owner_id: str
    location_id: str
    memory_id: str
    memory_summary: str
    importance: int
    relevance: float
    hint: str


class ItemUpdateResult(TypedDict, total=False):
    """Result for one item update action applied or skipped."""

    item_id: str
    action: str
    applied: bool
    reason: str
    memory_id: str


class _ItemCandidate(TypedDict, total=False):
    """Internal representation of an Item node and its optional anchored memory."""

    item_id: str
    item_name: str
    description: str
    owner_id: str
    location_id: str
    visibility: str
    emotional_weight: int
    last_seen_at: str
    memory_id: str
    memory_summary: str
    importance: int


class _ItemAction(TypedDict, total=False):
    """LLM-proposed item mutation constrained to existing item ids."""

    item_id: str
    action: Literal["anchor_memory", "move", "transfer_owner", "mark_lost", "update_description"]
    memory_summary: str
    importance: int
    new_location_id: str | None
    new_owner_id: str | None
    description_append: str | None
