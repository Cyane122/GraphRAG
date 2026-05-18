# ================================
# src/agents/manager/queries.py
#
# Graph query helpers used by the Manager pipeline.
#
# Functions
#   - fetch_character_data(char_id: str, scene_types: list[str]) -> dict : Fetch character graph context
#   - fetch_relationship_data(char_a: str, char_b: str) -> dict : Fetch relationship data
#   - fetch_recent_events(npc_id: str, pc_id: str, limit: int = 3) -> list[dict] : Fetch recent events
#   - fetch_location(char_id: str) -> str : Fetch a character's current location name
#   - fetch_location_hierarchy(loc_id: str) -> list[dict] : Fetch location + ancestors via PART_OF (most specific first)
#   - fetch_global_state(fallback_dt: datetime) -> dict : Fetch GlobalState
#   - get_location_name_from_id(location_id: str | None) -> str | None : Fetch a Location name
#   - detect_present_npcs(user_input: str, actor_response: str, known_npcs: list[dict]) -> list[str] : Detect NPCs present in the current scene
#   - fetch_location_character_ids(location_id: str | None) -> list[str] : Fetch characters located at the current scene location
#   - fetch_npc_profiles(npc_ids: list[str], main_npc_id: str, pc_id: str) -> list[dict] : Fetch secondary NPC profile, speech, and relationship data
# ================================
import json
from datetime import datetime

from src.agents.context.scene_keys import normalize_scene_types
from src.core.database import async_driver

REL_TO_KEY = {
    "HAS_PROFILE":           "static_profile",
    "HAS_PERSONALITY":       "personality",
    "HAS_STATE":             "dynamic_state",
    "HAS_INFO":              "dynamic_information",
    "HAS_INTIMATE":          "intimate_profile",
    "HAS_WORKPLACE":         "workplace_profile",
    "HAS_DIALOGUE_EXAMPLES": "dialogue_examples",
}

_BASE_RELS = ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE", "HAS_INFO"]

SCENE_REL_MAP: dict[str, list[str]] = {
    "daily":       _BASE_RELS,
    "bonding":     _BASE_RELS,
    "emotional":   _BASE_RELS,
    "vulnerable":  _BASE_RELS,
    "physical":    _BASE_RELS,
    "tense":       _BASE_RELS,
    "atmospheric": _BASE_RELS,
    "delusional":  _BASE_RELS,
    "hierarchy":   _BASE_RELS,
    "aggressive":  _BASE_RELS,
    "formal":      _BASE_RELS,
    "intimate":    [*_BASE_RELS, "HAS_INTIMATE"],
    "workplace":   [*_BASE_RELS, "HAS_WORKPLACE"],
    "aegyo":       _BASE_RELS,
}

async def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    """Fetch character graph context for normalized scene keys."""
    needed_rels: set[str] = {"HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"}
    for st in normalize_scene_types(scene_types):
        needed_rels.update(SCENE_REL_MAP.get(st, _BASE_RELS))

    result: dict = {}
    async with async_driver.session() as session:
        hub_rec = await session.run(
            "MATCH (c:Character {id: $char_id}) RETURN c AS props",
            char_id=char_id,
        )
        hub_row = await hub_rec.single()
        if hub_row:
            result.update(hub_row["props"])
        else:
            result["name"] = char_id

        for rel_type in needed_rels:
            rec = await session.run(f"""
                MATCH (c:Character {{id: $char_id}})-[:{rel_type}]->(n)
                RETURN n AS props
            """, char_id=char_id)
            row = await rec.single()
            if row:
                key = REL_TO_KEY.get(rel_type, rel_type.lower())
                raw = row["props"]
                # JSON blob 노드(StaticProfile 등)는 {"id":…, "props":"…json…"} 형태로 반환됨
                if isinstance(raw, dict) and isinstance(raw.get("props"), str):
                    try:
                        result[key] = json.loads(raw["props"])
                    except (ValueError, TypeError):
                        result[key] = raw
                else:
                    result[key] = raw
    return result


async def fetch_relationship_data(char_a: str, char_b: str) -> dict:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN r AS props
        """, a=char_a, b=char_b)
        row = await rec.single()
        return row["props"] if row else {}


async def fetch_recent_events(npc_id: str, pc_id: str, limit: int = 3) -> list[dict]:
    async with async_driver.session() as session:
        records = await session.run("""
            MATCH (npc:Character {id: $npc_id})-[:INVOLVED_IN]->(e:Event)
            OPTIONAL MATCH (npc)-[:REMEMBERS]->(m_npc:Memory)-[:OF_EVENT]->(e)
            OPTIONAL MATCH (pc:Character {id: $pc_id})-[:REMEMBERS]->(m_pc:Memory)-[:OF_EVENT]->(e)
            RETURN e.id                AS id,
                   CASE
                       WHEN e.narrative_summary IS NULL OR e.narrative_summary = '' THEN e.summary
                       ELSE e.narrative_summary
                   END                 AS summary,
                   e.timestamp         AS timestamp,
                   e.impact            AS impact,
                   e.memory_type       AS memory_type,
                   CASE
                       WHEN m_npc.narrative_summary IS NULL OR m_npc.narrative_summary = '' THEN m_npc.summary
                       ELSE m_npc.narrative_summary
                   END                 AS npc_memory,
                   CASE
                       WHEN m_pc.narrative_summary IS NULL OR m_pc.narrative_summary = '' THEN m_pc.summary
                       ELSE m_pc.narrative_summary
                   END                 AS pc_memory
            ORDER BY e.timestamp DESC
            LIMIT $limit
        """, npc_id=npc_id, pc_id=pc_id, limit=limit)
        rows = await records.data()
        return [dict(r) for r in rows]


async def fetch_location(char_id: str) -> str:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)
            RETURN l.name AS name
        """, char_id=char_id)
        row = await rec.single()
        return row["name"] if row else "알 수 없는 장소"


async def fetch_global_state(fallback_dt: datetime) -> dict:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime       AS currentTime,
                   gs.weather           AS weather,
                   gs.currentLocationId AS currentLocationId
        """)
        row = await rec.single()
        if row and row.get("currentTime"):
            return dict(row)
    return {
        "currentTime":       fallback_dt.isoformat(),
        "weather":           "Clear",
        "currentLocationId": "알 수 없는 장소",
    }


async def _get_allowed_locations() -> str:
    async with async_driver.session() as session:
        result  = await session.run("MATCH (l:Location) RETURN l.id AS id, l.name AS name")
        records = await result.data()
        locs    = [f'- "{r["id"]}" ({r["name"]})' for r in records]
        return "\n".join(locs) if locs else "- No registered locations."


async def fetch_location_hierarchy(loc_id: str) -> list[dict]:
    """Fetch location node + all ancestors via PART_OF, from most specific to most general."""
    if not loc_id:
        return []

    _FIELDS = """
        l.id AS id, l.name AS name, l.description AS description,
        l.atmosphere AS atmosphere, l.prompt_hint AS prompt_hint,
        l.prompt_priority AS prompt_priority
    """
    nodes = []
    current_id = loc_id

    async with async_driver.session() as session:
        for _ in range(4):
            rec = await session.run(
                f"MATCH (l:Location {{id: $id}}) RETURN {_FIELDS}", id=current_id
            )
            row = await rec.single()
            if not row:
                break
            nodes.append(dict(row))

            parent_rec = await session.run("""
                MATCH (l:Location {id: $id})-[:PART_OF]->(p:Location)
                RETURN p.id AS id
            """, id=current_id)
            parent_row = await parent_rec.single()
            if not parent_row:
                break
            current_id = parent_row["id"]

    return nodes


async def get_location_name_from_id(location_id: str | None) -> str | None:
    if not location_id:
        return None
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (l:Location {id: $id}) RETURN l.name AS name", id=location_id
        )
        record = await result.single()
        return record["name"] if record else None


def detect_present_npcs(
    user_input:   str,
    recent_story: str,
    npc_name_map: dict[str, str],
) -> list[str]:
    text  = f"{user_input} {recent_story}"
    found: set[str] = set()
    for keyword, char_id in npc_name_map.items():
        if keyword in text:
            found.add(char_id)
    if found:
        print(f"[NPC 감지] {sorted(found)}")
    return sorted(found)


async def fetch_location_character_ids(location_id: str | None) -> list[str]:
    """Fetch character ids explicitly located at the current scene location."""
    if not location_id:
        return []

    async with async_driver.session() as session:
        records = await session.run("""
            MATCH (c:Character)-[:LOCATED_AT]->(:Location {id: $location_id})
            RETURN c.id AS id
            ORDER BY c.id
        """, location_id=location_id)
        rows = await records.data()
    return [str(row["id"]) for row in rows if row.get("id")]


async def fetch_npc_profiles(
    npc_ids:     list[str],
    main_npc_id: str,
    pc_id:       str,
) -> list[dict]:
    """Fetch secondary NPC prompt context, including personality and prompt profiles."""
    results = []
    async with async_driver.session() as session:
        for nid in npc_ids:
            if nid in (main_npc_id, pc_id):
                continue
            name_rec = await session.run(
                "MATCH (c:Character {id: $id}) RETURN c.name AS name, c.aliases AS aliases", id=nid
            )
            name_row = await name_rec.single()
            if not name_row:
                continue

            profile_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PROFILE]->(p)
                RETURN p AS props
            """, id=nid)
            profile_row = await profile_rec.single()

            personality_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PERSONALITY]->(p)
                RETURN p AS props
            """, id=nid)
            personality_row = await personality_rec.single()

            rel_rec = await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                RETURN r AS props
            """, a=main_npc_id, b=nid)
            rel_row = await rel_rec.single()

            pc_rel_rec = await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                RETURN r AS props
            """, a=nid, b=pc_id)
            pc_rel_row = await pc_rel_rec.single()

            state_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_STATE]->(d:DynamicState)
                RETURN d AS props
            """, id=nid)
            state_row = await state_rec.single()

            info_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_INFO]->(n)
                RETURN n AS props
            """, id=nid)
            info_row = await info_rec.single()

            speech_rec = await session.run("""
                MATCH (s:SpeechProfile)
                WHERE s.char_id = $id
                  AND (s.audience_id = '' OR s.audience_id = $pc_id OR s.audience_id IS NULL)
                RETURN s.id AS id,
                       s.name AS name,
                       s.summary AS summary,
                       s.prompt_hint AS prompt_hint,
                       s.prompt_priority AS prompt_priority,
                       s.tags AS tags
                ORDER BY s.prompt_priority DESC
                LIMIT 3
            """, id=nid, pc_id=pc_id)
            speech_rows = await speech_rec.data()

            relationship_profile_rec = await session.run("""
                MATCH (rp:RelationshipProfile)
                WHERE rp.source_id = $id
                  AND (rp.target_id = $pc_id OR rp.target_id = $main_npc_id)
                RETURN rp.id AS id,
                       rp.name AS name,
                       rp.summary AS summary,
                       rp.prompt_hint AS prompt_hint,
                       rp.prompt_priority AS prompt_priority,
                       rp.tags AS tags,
                       rp.target_id AS target_id
                ORDER BY rp.prompt_priority DESC
                LIMIT 3
            """, id=nid, pc_id=pc_id, main_npc_id=main_npc_id)
            relationship_profile_rows = await relationship_profile_rec.data()

            results.append({
                "char_id":             nid,
                "name":                name_row["name"],
                "aliases":             name_row.get("aliases") or [],
                "profile":             _decode_json_props(profile_row["props"] if profile_row else {}),
                "dynamic_information": _decode_json_props(info_row["props"] if info_row else {}),
                "personality":         _decode_json_props(personality_row["props"] if personality_row else {}),
                "speech_profiles":     [_clean_record(row) for row in speech_rows],
                "relationship_profiles": [_clean_record(row) for row in relationship_profile_rows],
                "rel_to_npc":          rel_row["props"] if rel_row else {},
                "rel_to_pc":           pc_rel_row["props"] if pc_rel_row else {},
                "dynamic_state":       dict(state_row["props"]) if state_row else {},
            })
    return results


def _decode_json_props(raw: dict) -> dict:
    """Decode JSON-backed static node props while preserving plain Kuzu records."""
    if isinstance(raw, dict) and isinstance(raw.get("props"), str):
        try:
            return json.loads(raw["props"])
        except (ValueError, TypeError):
            return raw
    return raw if isinstance(raw, dict) else {}


def _clean_record(record: dict) -> dict:
    """Drop empty prompt-profile fields before rendering."""
    return {
        key: value
        for key, value in record.items()
        if value not in (None, "", [])
    }
