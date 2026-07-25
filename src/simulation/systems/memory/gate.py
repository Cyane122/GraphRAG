# ================================
# src/simulation/systems/memory/gate.py
#
# Deterministic memory creation gate — decides reject/create/reinforce per Memory.
#
# Classes
#   - GateDecision : Gate outcome enum (reject / create / reinforce / update / resolve)
#
# Functions
#   - decide_gate(importance: int, signals: list[str], memory_type: str, gossip_meta: dict | None = None) -> GateDecision : Pure rule-based gate (no DB access)
#   - apply_gate(char_id: str, event_id: str, importance: int, signals: list[str], memory_type: str, source_commit_id: str, gossip_meta: dict | None = None) -> tuple[GateDecision, str] : Full gate with DB duplicate check; returns (decision, target_mem_id_to_reinforce)
# ================================

from enum import Enum

from src.core.database import async_driver


# Signals that override the importance threshold and allow memory creation.
STRONG_SIGNALS: frozenset[str] = frozenset({
    "promise", "appointment", "secret", "first_time",
    "misunderstanding", "conflict", "reconciliation", "betrayal",
    "boundary", "gift", "item_anchor", "debt", "favor",
    "identity", "emotional_wound",
})

# memory_type values that act as implicit strong signals even without explicit signal tags.
_IMPLICIT_SIGNAL_TYPES: frozenset[str] = frozenset({"emotional", "relational"})


class GateDecision(str, Enum):
    REJECT = "reject"
    CREATE = "create"
    REINFORCE = "reinforce"
    UPDATE = "update"
    RESOLVE = "resolve"


def _effective_strong_signal(
    signals: list[str],
    memory_type: str,
    gossip_meta: dict | None,
) -> bool:
    """True if at least one qualifying strong signal is active."""
    signal_set = set(signals or [])
    if signal_set & STRONG_SIGNALS:
        return True
    # emotional/relational type carries implicit signal weight
    if memory_type in _IMPLICIT_SIGNAL_TYPES:
        return True
    # gossip qualifies only when it has a named source or measurable relationship impact
    if "gossip" in signal_set:
        meta = gossip_meta or {}
        return bool(meta.get("named_source") or meta.get("relationship_impact"))
    return False


def decide_gate(
    importance: int,
    signals: list[str],
    memory_type: str,
    gossip_meta: dict | None = None,
) -> GateDecision:
    """
    Pure importance + signal gate. No DB access.

    Returns CREATE or REJECT only — reinforce/update/resolve require
    the subsequent DB duplicate check in apply_gate.
    """
    has_strong = _effective_strong_signal(signals, memory_type, gossip_meta)

    if importance < 3 and not has_strong:
        return GateDecision.REJECT
    # importance 3-4 needs an explicit signal to justify creation
    if importance <= 4 and not has_strong:
        return GateDecision.REJECT
    return GateDecision.CREATE


async def apply_gate(
    char_id: str,
    event_id: str,
    importance: int,
    signals: list[str],
    memory_type: str,
    source_commit_id: str = "",
    gossip_meta: dict | None = None,
) -> tuple[GateDecision, str]:
    """
    Full gate with DB duplicate check.

    Returns (decision, target_mem_id) where target_mem_id is the memory
    node id to reinforce when decision == REINFORCE. Empty string otherwise.

    1. Apply importance/signal rules (decide_gate).
    2. If CREATE: check for existing Memory with same event_id → REINFORCE.
    3. If CREATE and source_commit_id given: check for same-commit Memory → REINFORCE.
       Returns the actual matched mem_id so the caller reinforces the right node.
    """
    decision = decide_gate(importance, signals, memory_type, gossip_meta)
    if decision == GateDecision.REJECT:
        return decision, ""

    # Dedup: same event_id for this char → memory already exists → REINFORCE
    mem_id = f"mem_{char_id}_{event_id}"
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (m:Memory {id: $mid}) RETURN m.id AS id",
            mid=mem_id,
        )
        if await rec.single():
            return GateDecision.REINFORCE, mem_id

    # Dedup: same source_commit_id already produced a Memory for this char → REINFORCE.
    # Return the matched memory's actual id so the caller can reinforce the correct node.
    if source_commit_id:
        async with async_driver.session() as session:
            rec2 = await session.run("""
                MATCH (c:Character {id: $char_id})-[:REMEMBERS]->(m:Memory)
                WHERE m.source_commit_id = $commit_id
                  AND m.source_commit_id <> ''
                RETURN m.id AS id
                LIMIT 1
            """, char_id=char_id, commit_id=source_commit_id)
            row = await rec2.single()
            if row:
                return GateDecision.REINFORCE, str(row["id"])

    return GateDecision.CREATE, ""
