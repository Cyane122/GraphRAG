# ================================
# src/simulation/systems/social/relationships.py
#
# Seed conservative directed relationships for characters sharing a Social-system scene.
#
# Functions
#   - ensure_scene_relationships(participant_ids: list[str]) -> None : Ensure directed relationships between durable scene participants
# ================================
from src.core.database import async_driver
from src.core.database.helpers import ensure_relationship
from src.simulation.systems.social.stub_persist import ensure_character_runtime_nodes

def _unique_ordered(values: list[str]) -> list[str]:
    """Return non-empty ids in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

def _initial_relationship_for_pair(
    source_id: str,
    target_id: str,
    source_type: str = "",
    target_type: str = "",
) -> dict:
    """Pick a conservative first-encounter relationship seed."""
    if source_id == target_id:
        return {}
    if "player" in {source_id, target_id}:
        return {
            "type": "acquaintance",
            "affinity": 0,
            "trust": 5,
            "current_status": "newly aware of each other",
        }
    if "transient" in {source_type, target_type}:
        return {
            "type": "acquaintance",
            "affinity": 0,
            "trust": 10,
            "current_status": "first encounter",
        }
    return {
        "type": "acquaintance",
        "affinity": 5,
        "trust": 15,
        "current_status": "lightly established acquaintance",
    }

async def _fetch_character_types(char_ids: list[str]) -> dict[str, str]:
    """Fetch Character.type values for relationship seeding."""
    result: dict[str, str] = {}
    async with async_driver.session() as session:
        for char_id in char_ids:
            rec = await session.run(
                "MATCH (c:Character {id: $cid}) RETURN c.type AS type",
                cid=char_id,
            )
            row = await rec.single()
            result[char_id] = str(row["type"] or "") if row else ""
    return result

async def ensure_scene_relationships(participant_ids: list[str]) -> None:
    """Ensure directed relationships exist between every character in a scene."""
    participants = _unique_ordered(participant_ids)
    if len(participants) < 2:
        return

    char_types = await _fetch_character_types(participants)
    durable_participants = [
        char_id
        for char_id in participants
        if char_types.get(char_id, "") != "transient"
    ]
    if len(durable_participants) < 2:
        return

    for char_id in durable_participants:
        await ensure_character_runtime_nodes(char_id)

    for source_id in durable_participants:
        for target_id in durable_participants:
            if source_id == target_id:
                continue
            seed = _initial_relationship_for_pair(
                source_id,
                target_id,
                char_types.get(source_id, ""),
                char_types.get(target_id, ""),
            )
            await ensure_relationship(
                source_id,
                target_id,
                rel_type=seed.get("type", "acquaintance"),
                affinity=int(seed.get("affinity", 0)),
                trust=int(seed.get("trust", 10)),
                current_status=seed.get("current_status", "first encounter"),
            )
