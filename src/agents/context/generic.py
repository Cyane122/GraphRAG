# ================================
# src/agents/context/generic.py
#
# Retrieves minimal generic prompt nodes for the dynamic Actor context.
#
# Functions
#   - fetch_generic_prompt_context(npc_id: str, pc_id: str, location_id: str | None, scene_type: str, user_input: str = "") -> dict : fetch Location, Rule, SpeechProfile, and RelationshipProfile hints
# ================================

import json

from src.agents.context.scene_keys import normalize_scene_type
from src.agents.context.transient import sanitize_location_hints_for_turn
from src.core.database import async_driver


async def fetch_generic_prompt_context(
    npc_id: str,
    pc_id: str,
    location_id: str | None,
    scene_type: str,
    user_input: str = "",
) -> dict:
    """Fetch generic prompt nodes for the current scene, skipping missing data."""
    location, rules, speech_profiles, relationship_profiles = {}, [], [], []
    context_scene_type = normalize_scene_type(scene_type)

    async with async_driver.session() as session:
        if location_id:
            location = await _fetch_location_hint(session, location_id)
            if location:
                sanitized_locations = sanitize_location_hints_for_turn([location], user_input)
                location = sanitized_locations[0] if sanitized_locations else location
        rules = await _fetch_rule_hints(session, location_id or "", npc_id, context_scene_type)
        speech_profiles = await _fetch_speech_profiles(session, npc_id, pc_id, context_scene_type)
        relationship_profiles = await _fetch_relationship_profiles(session, npc_id, pc_id, context_scene_type)

    return {
        "location_profile": location,
        "rules": rules,
        "speech_profiles": speech_profiles,
        "relationship_profiles": relationship_profiles,
    }


async def _fetch_location_hint(session, location_id: str) -> dict:
    """Load the active Location as a prompt-hint record."""
    try:
        result = await session.run(
            """
            MATCH (l:Location {id: $location_id})
            RETURN l.id AS id,
                   l.name AS name,
                   l.summary AS summary,
                   l.prompt_hint AS prompt_hint,
                   l.prompt_priority AS prompt_priority,
                   l.tags AS tags,
                   l.description AS description,
                   l.atmosphere AS atmosphere
            """,
            location_id=location_id,
        )
        record = await result.single()
    except Exception:
        return {}

    return _clean_record(dict(record)) if record else {}


async def _fetch_rule_hints(session, location_id: str, npc_id: str, scene_type: str) -> list[dict]:
    """Load only active rules matching the location, character, or scene type."""
    try:
        result = await session.run(
            """
            MATCH (r:Rule)
            WHERE (r.status = '' OR r.status = 'active' OR r.status IS NULL)
              AND (r.location_id = '' OR r.location_id = $location_id OR r.location_id IS NULL)
              AND (r.owner_id = '' OR r.owner_id = $npc_id OR r.owner_id IS NULL)
              AND (r.scene_type = '' OR r.scene_type = $scene_type OR r.scene_type IS NULL)
            RETURN r.id AS id,
                   r.name AS name,
                   r.summary AS summary,
                   r.prompt_hint AS prompt_hint,
                   r.prompt_priority AS prompt_priority,
                   r.tags AS tags
            ORDER BY r.prompt_priority DESC
            LIMIT 4
            """,
            location_id=location_id,
            npc_id=npc_id,
            scene_type=scene_type,
        )
        rows = await result.data()
    except Exception:
        return []

    return [_clean_record(row) for row in rows]


async def _fetch_speech_profiles(session, npc_id: str, pc_id: str, scene_type: str) -> list[dict]:
    """Load speech-profile nodes, falling back to Personality.speech_style when absent."""
    profiles: list[dict] = []
    try:
        result = await session.run(
            """
            MATCH (s:SpeechProfile)
            WHERE s.char_id = $npc_id
              AND (s.audience_id = '' OR s.audience_id = $pc_id OR s.audience_id IS NULL)
              AND (s.scene_type = '' OR s.scene_type = $scene_type OR s.scene_type IS NULL)
            RETURN s.id AS id,
                   s.name AS name,
                   s.summary AS summary,
                   s.prompt_hint AS prompt_hint,
                   s.prompt_priority AS prompt_priority,
                   s.tags AS tags
            ORDER BY s.prompt_priority DESC
            LIMIT 3
            """,
            npc_id=npc_id,
            pc_id=pc_id,
            scene_type=scene_type,
        )
        profiles = [_clean_record(row) for row in await result.data()]
    except Exception:
        profiles = []

    if profiles:
        return profiles
    return await _fetch_personality_speech_fallback(session, npc_id)


async def _fetch_relationship_profiles(session, npc_id: str, pc_id: str, scene_type: str) -> list[dict]:
    """Load relationship-profile nodes for the main NPC and PC pair."""
    try:
        result = await session.run(
            """
            MATCH (rp:RelationshipProfile)
            WHERE rp.source_id = $npc_id
              AND rp.target_id = $pc_id
              AND (rp.scene_type = '' OR rp.scene_type = $scene_type OR rp.scene_type IS NULL)
            RETURN rp.id AS id,
                   rp.name AS name,
                   rp.summary AS summary,
                   rp.prompt_hint AS prompt_hint,
                   rp.prompt_priority AS prompt_priority,
                   rp.tags AS tags
            ORDER BY rp.prompt_priority DESC
            LIMIT 3
            """,
            npc_id=npc_id,
            pc_id=pc_id,
            scene_type=scene_type,
        )
        rows = await result.data()
    except Exception:
        return []

    return [_clean_record(row) for row in rows]


async def _fetch_personality_speech_fallback(session, npc_id: str) -> list[dict]:
    """Expose legacy Personality.speech_style as a SpeechProfile-shaped hint."""
    try:
        result = await session.run(
            """
            MATCH (c:Character {id: $npc_id})-[:HAS_PERSONALITY]->(p:Personality)
            RETURN p.props AS props
            LIMIT 1
            """,
            npc_id=npc_id,
        )
        record = await result.single()
    except Exception:
        return []

    if not record:
        return []
    props = _parse_json_object(record.get("props"))
    speech_style = props.get("speech_style")
    if not speech_style:
        return []
    return [{
        "id": f"{npc_id}_speech_fallback",
        "name": "legacy speech style",
        "prompt_hint": speech_style,
        "prompt_priority": 0,
        "tags": ["legacy"],
    }]


def _parse_json_object(raw: str | None) -> dict:
    """Parse JSON object strings defensively."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_record(record: dict) -> dict:
    """Drop empty Kuzu values before rendering."""
    return {
        key: value
        for key, value in record.items()
        if value not in (None, "", [])
    }
