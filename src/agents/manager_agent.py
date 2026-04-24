import os
from datetime import datetime
from importlib import import_module
from typing import Any

from src.prompt.promptBuilder import PromptBuilder
from src.graph.world.default import World
from src.utils.db_utils import async_driver, fetch_similar_events
from src.utils.llm_utils import extract_json_from_llm, llm_client
from src.utils.embedder import embed_async

CLASSIFIER_MODEL = os.getenv("MODEL_CLASSIFIER", "claude-haiku-4-5-20251001")

REL_TO_KEY = {
    "HAS_PROFILE":           "static_profile",
    "HAS_PERSONALITY":       "personality",
    "HAS_STATE":             "dynamic_state",
    "HAS_INTIMATE":          "intimate_profile",
    "HAS_WORKPLACE":         "workplace_profile",
    "HAS_DIALOGUE_EXAMPLES": "dialogue_examples",
}

SCENE_REL_MAP: dict[str, list[str]] = {
    "daily":     ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
    "emotional": ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
    "physical":  ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
    "intimate":  ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE", "HAS_INTIMATE"],
    "workplace": ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE", "HAS_WORKPLACE"],
    "aegyo":     ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
}


# ════════════════════════════════════════════════════════════
# 씬 분류
# ════════════════════════════════════════════════════════════

async def classify_scene(user_input: str, recent_story: str) -> list[str]:
    prompt = f"""You are a scene classifier for a roleplay system.
Analyze the user input and recent story, return a JSON array of scene types.

Possible types: daily / emotional / physical / intimate / workplace / aegyo
Multiple types allowed. Return ONLY a JSON array. No explanation, no markdown.
Example: ["daily", "emotional"]

Recent story:
{recent_story}

User input:
{user_input}"""

    try:
        response = llm_client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=64,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        scene_types = extract_json_from_llm(raw)
        if not isinstance(scene_types, list):
            raise ValueError("not a list")
        print(f"[씬 분류 / {CLASSIFIER_MODEL}] {scene_types}")
        return scene_types
    except Exception as e:
        print(f"[씬 분류 실패] {e} → 폴백: ['daily']")
        return ["daily"]


# ════════════════════════════════════════════════════════════
# 세계 인스턴스 로드
# ════════════════════════════════════════════════════════════

def load_world_instance(world_id: str) -> World:
    try:
        module = import_module(f"src.graph.world.{world_id}")
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
                return obj()
    except Exception as e:
        print(f"[WorldLoader] {world_id} 로드 실패: {e}")
    return World()


# ════════════════════════════════════════════════════════════
# DB 조회 함수
# ════════════════════════════════════════════════════════════

async def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    needed_rels: set[str] = {"HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"}
    for st in scene_types:
        needed_rels.update(SCENE_REL_MAP.get(st, []))

    result: dict = {}
    async with async_driver.session() as session:
        hub_rec = await session.run(
            "MATCH (c:Character {id: $char_id}) RETURN properties(c) AS props",
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
                RETURN properties(n) AS props
            """, char_id=char_id)
            row = await rec.single()
            if row:
                key = REL_TO_KEY.get(rel_type, rel_type.lower())
                result[key] = row["props"]
    return result


async def fetch_relationship_data(char_a: str, char_b: str) -> dict:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN properties(r) AS props
        """, a=char_a, b=char_b)
        row = await rec.single()
        return row["props"] if row else {}


async def fetch_recent_events(char_id: str, limit: int = 3) -> list[dict]:
    async with async_driver.session() as session:
        records = await session.run("""
            MATCH (c:Character {id: $char_id})-[:INVOLVED_IN]->(e:Event)
            RETURN e.id AS id, e.summary AS summary,
                   e.timestamp AS timestamp, e.impact AS impact
            ORDER BY e.timestamp DESC
            LIMIT $limit
        """, char_id=char_id, limit=limit)
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


def detect_present_npcs(
    user_input: str,
    recent_story: str,
    npc_name_map: dict[str, str],
) -> list[str]:
    text = f"{user_input} {recent_story}"
    found: set[str] = set()
    for keyword, char_id in npc_name_map.items():
        if keyword in text:
            found.add(char_id)
    if found:
        print(f"[NPC 감지] {sorted(found)}")
    return sorted(found)


async def fetch_npc_profiles(
    npc_ids: list[str],
    main_npc_id: str,
    pc_id: str,
) -> list[dict]:
    results = []
    async with async_driver.session() as session:
        for npc_id in npc_ids:
            name_rec = await session.run(
                "MATCH (c:Character {id: $id}) RETURN c.name AS name", id=npc_id
            )
            name_row = await name_rec.single()
            if not name_row:
                continue

            profile_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PROFILE]->(p)
                RETURN properties(p) AS props
            """, id=npc_id)
            profile_row = await profile_rec.single()

            rel_rec = await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                RETURN properties(r) AS props
            """, a=main_npc_id, b=npc_id)
            rel_row = await rel_rec.single()

            results.append({
                "char_id":    npc_id,
                "name":       name_row["name"],
                "profile":    profile_row["props"] if profile_row else {},
                "rel_to_npc": rel_row["props"]     if rel_row     else {},
            })
    return results


async def get_location_name_from_id(location_id: str | None) -> str | None:
    if not location_id:
        return None
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (l:Location {id: $id}) RETURN l.name AS name", id=location_id
        )
        record = await result.single()
        return record["name"] if record else None


# ════════════════════════════════════════════════════════════
# 메인 파이프라인
# ════════════════════════════════════════════════════════════

async def run_manager(
    user_input: str,
    pc_id: str,
    npc_id: str,
    recent_story: str = "",
    world_id: str = None,
) -> tuple[str, str, str, list[str]]:

    world        = load_world_instance(world_id)
    world_config = world.get_full_config()
    start_dt     = world_config.get("start_time")

    global_state = await fetch_global_state(start_dt)
    scene_types  = await classify_scene(user_input, recent_story)

    # ── DB 병렬 조회 ─────────────────────────────────────
    db_fetch_tasks = [
        fetch_character_data(npc_id, scene_types),
        fetch_character_data(pc_id,  scene_types),
        fetch_relationship_data(pc_id, npc_id),
        fetch_recent_events(npc_id, limit=3),
    ]
    db_results: Any = await asyncio.gather(*db_fetch_tasks)
    char_data, user_data, relationship, recent_events = db_results

    # ── Vector 유사 검색 (recall_events) ─────────────────
    recall_events: list[dict] = []
    try:
        query_embedding = await embed_async(user_input)
        recall_candidates = await fetch_similar_events(
            char_id=npc_id,
            query_embedding=query_embedding,
            limit=2,
        )
        # recent_events와 중복 제거
        recent_ids = {e["id"] for e in recent_events}
        recall_events = [e for e in recall_candidates if e["id"] not in recent_ids]
    except Exception as e:
        print(f"[Manager] recall_events 조회 실패 (무시): {e}")

    # ── 위치 정보 ─────────────────────────────────────────
    current_dt    = datetime.fromisoformat(global_state.get("currentTime"))
    loc_id        = global_state.get("currentLocationId")
    location_name = await get_location_name_from_id(loc_id) or await fetch_location(npc_id)

    if "dynamic_state" in char_data:
        char_data["dynamic_state"]["location_id"] = location_name

    # ── 씬 내 NPC 감지 ───────────────────────────────────
    present_npc_ids = detect_present_npcs(user_input, recent_story, world.get_npc_name_map())
    npcs = await fetch_npc_profiles(present_npc_ids, npc_id, pc_id) if present_npc_ids else []

    # ── 프롬프트 조립 ─────────────────────────────────────
    builder = PromptBuilder(world_config, char_data.get("name"), user_data.get("name"))

    fixed_prompt, genre_prompt, dynamic_prompt = builder.build(
        scene_types=scene_types,
        char_data=char_data,
        relationship=relationship,
        events=[
            {"timestamp": e["timestamp"], "summary": e["summary"]}
            for e in recent_events
        ],
        recall_events=[
            {"timestamp": e["timestamp"], "summary": e["summary"], "score": round(e.get("score", 0), 3)}
            for e in recall_events
        ],
        recent_story=recent_story,
        user_input=user_input,
        location=location_name,
        npcs=npcs,
        dt=current_dt,
    )

    return fixed_prompt, genre_prompt, dynamic_prompt, scene_types


if __name__ == "__main__":
    import asyncio

    async def _test():
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input="*지희와 아린이 놀러 왔다. 은서와 셋이 소파에 앉아 수다를 떤다.*",
            pc_id="sian", npc_id="eun_seo",
            recent_story="토요일 오후, 은서네 집.",
            world_id="babe_univ",
        )
        print("=== FIXED ==="); print(fixed[:200], "...\n")
        print("=== GENRE ==="); print(genre[:200] if genre else "(없음)", "\n")
        print("=== DYNAMIC ==="); print(dynamic)
        print("\n=== 씬 타입 ==="); print(scene_types)

    asyncio.run(_test())