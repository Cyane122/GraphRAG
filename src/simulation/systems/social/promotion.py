# ================================
# src/simulation/systems/social/promotion.py
#
# Decide whether a transient NPC should be promoted to a named NPC.
#
# Functions
#   - check_and_promote(char_id: str, main_npc_id: str, event_importance: int) -> bool : Promote to named NPC based on appearance and importance
# ================================
import json

from src.config import MODEL_STATE_UPDATER as BUILDER_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
from src.simulation.systems.needs import ensure_traits

PROMOTE_APPEARANCE_COUNT = 3
PROMOTE_IMPORTANCE = 5
PROMOTE_AFFINITY_ABS = 40

async def check_and_promote(
    char_id:          str,
    main_npc_id:      str,
    event_importance: int,
) -> bool:
    """승격 기준 충족 시 Transient → Named NPC로 승격한다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props_json, c.type AS type
        """, cid=char_id)
        row = await rec.single()
        if not row or row["type"] != "transient":
            return False
        props: dict = {}
        if row["props_json"]:
            try:
                props = json.loads(row["props_json"])
            except (ValueError, TypeError):
                pass
        count = props.get("appearance_count", 0)

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $main})-[r:RELATIONSHIP]->(b:Character {id: $cid})
            RETURN r.affinity AS affinity
        """, main=main_npc_id, cid=char_id)
        rel_row  = await rec.single()
        affinity = abs(rel_row["affinity"] or 0) if rel_row else 0

    should_promote = (
        count >= PROMOTE_APPEARANCE_COUNT
        or event_importance >= PROMOTE_IMPORTANCE
        or affinity >= PROMOTE_AFFINITY_ABS
    )
    if should_promote:
        await _promote_to_named(char_id)
        return True
    return False


async def _promote_to_named(char_id: str) -> None:
    """Transient → Named NPC 승격. Personality + NeedsState 생성."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event)
            RETURN e.summary AS summary, e.importance AS importance
            ORDER BY e.importance DESC LIMIT 5
        """, cid=char_id)
        events = await rec.data()

        rec2 = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props_json
        """, cid=char_id)
        row     = await rec2.single()
        profile: dict = {}
        if row and row["props_json"]:
            try:
                profile = json.loads(row["props_json"])
            except (ValueError, TypeError):
                pass

    event_summaries = "\n".join(
        f"- {e['summary']} (importance {e['importance']})"
        for e in events if e.get("summary")
    ) or "(없음)"

    system_instruction = "Generate a Personality profile for an NPC in a Korean slice-of-life roleplay."

    prompt = f"""Character: {profile.get('name_kor', char_id)}
Known personality: {profile.get('personality', '')}
Context: {profile.get('context', '')}
Events: {event_summaries}

Return ONLY JSON:
{{
  "core_traits":         "keyword+keyword+keyword (English, plus-separated)",
  "speech_style":        "1 sentence",
  "habit_when_thinking": "physical habit",
  "sample_line":         "한 줄 대사 (Korean)"
}}"""

    personality_data: dict = {"core_traits": profile.get("personality", "unknown")}
    try:
        model = get_model(BUILDER_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"max_output_tokens": 1024, "response_mime_type": "application/json", "log_source": "social_promotion"},
        )
        parsed = extract_json_from_llm(resp.text)
        if isinstance(parsed, dict):
            personality_data = parsed
    except Exception as e:
        print(f"[WorldBuilder] Personality 생성 실패: {e}")

    async with async_driver.session() as session:
        # Personality 스키마도 (id, props) JSON blob 구조
        personality_json = json.dumps({
            "core_traits":         personality_data.get("core_traits", ""),
            "speech_style":        personality_data.get("speech_style", ""),
            "habit_when_thinking": personality_data.get("habit_when_thinking", ""),
            "sample_line":         personality_data.get("sample_line", ""),
        }, ensure_ascii=False)
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PERSONALITY]->(:Personality {id: $pid, props: $props_json})
        """,
            cid        = char_id,
            pid        = f"{char_id}_personality",
            props_json = personality_json,
        )

        await session.run("""
            MATCH (c:Character {id: $cid})
            WHERE NOT (c)-[:HAS_NEEDS]->()
            CREATE (c)-[:HAS_NEEDS]->(:NeedsState {
                id:     $nid,
                hunger: 0.3, rest:   0.2, social: 0.2,
                fun:    0.3, safety: 0.05, libido: 0.15
            })
        """, cid=char_id, nid=f"{char_id}_needs")

        # c.type만 갱신 — StaticProfile은 JSON blob이라 별도 read-modify-write 불필요
        await session.run("""
            MATCH (c:Character {id: $cid})
            SET c.type = "named"
        """, cid=char_id)

    try:
        await ensure_traits(char_id)
    except Exception as e:
        print(f"[WorldBuilder] traits 생성 실패: {e}")

    print(f"[WorldBuilder] ★ Named NPC 승격: {char_id}")
