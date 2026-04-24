# src/agents/manager_agent.py

import os
from openai import OpenAI
import asyncio
from datetime import datetime
from src.prompt.promptBuilder import PromptBuilder
from importlib import import_module
from src.graph.world.default import World
from src.utils.db_utils import async_driver
from src.utils.llm_utils import extract_json_from_llm, llm_client
from typing import Any

CLASSIFIER_MODEL = os.getenv("MODEL_CLASSIFIER", "claude-haiku-4-5-20251001")

# ════════════════════════════════════════════════════════════
# 1단계: 씬 분류
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
# 2단계: World Instance 사용
# ════════════════════════════════════════════════════════════
def load_world_instance(world_id: str) -> World:
    try:
        module = import_module(f"src.graph.world.{world_id}")
        return module.world_instance
    except (ModuleNotFoundError, AttributeError) as e:
        from src.graph.world.default import World
        return World()

# ════════════════════════════════════════════════════════════
# 3단계: Graph 데이터 추출
# ════════════════════════════════════════════════════════════

SCENE_NODE_MAP = {
    "daily":     ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
    "emotional": ["HAS_PERSONALITY", "HAS_STATE"],
    "physical":  ["HAS_PROFILE", "HAS_STATE"],
    "intimate":  ["HAS_PROFILE", "HAS_STATE", "HAS_INTIMATE"],
    "workplace": ["HAS_PROFILE", "HAS_STATE", "HAS_WORKPLACE"],
    "aegyo":     ["HAS_PERSONALITY", "HAS_STATE"],
}

REL_TO_KEY = {
    "HAS_PROFILE":     "static_profile",
    "HAS_PERSONALITY": "personality",
    "HAS_STATE":       "dynamic_state",
    "HAS_INTIMATE":    "intimate_profile",
    "HAS_WORKPLACE":   "workplace_profile",
}

async def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    needed_rels = set()
    for st in scene_types:
        needed_rels.update(SCENE_NODE_MAP.get(st, []))

    result = {}
    async with async_driver.session() as session:
        # ── 1. 허브 노드(:Character) 자체의 속성(name 등) 가져오기 ──
        hub_record = await session.run("""
            MATCH (c:Character {id: $char_id})
            RETURN properties(c) AS props
        """, char_id=char_id)
        hub_record = await hub_record.single()
        if hub_record:
            # result의 최상위 레벨에 name, id 등의 기본 정보를 먼저 넣습니다.
            result.update(hub_record["props"])
        else:
            result["name"] = char_id

        # ── 2. 서브 노드 데이터 가져오기 (기존 로직) ──
        for rel_type in needed_rels:
            records = await session.run(f"""
                MATCH (c:Character {{id: $char_id}})-[:{rel_type}]->(n)
                RETURN properties(n) AS props
            """, char_id=char_id)
            record = await records.single()
            if record:
                key = REL_TO_KEY.get(rel_type, rel_type.lower())
                result[key] = record["props"]
    return result


async def fetch_relationship_data(char_a: str, char_b: str) -> dict:
    async with async_driver.session() as session:
        record = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN properties(r) AS props
        """, a=char_a, b=char_b)
        record = await record.single()
        return record["props"] if record else {}


async def fetch_recent_events(char_id: str, limit: int = 3) -> list[dict]:
    async with async_driver.session() as session:
        records = await session.run("""
            MATCH (c:Character {id: $char_id})-[:INVOLVED_IN]->(e:Event)
            RETURN e.id AS id, e.summary AS summary,
                   e.timestamp AS timestamp, e.impact AS impact
            ORDER BY e.timestamp DESC
            LIMIT $limit
        """, char_id=char_id, limit=limit)
        records = await records.data()
        return [dict(r) for r in records]


async def fetch_location(char_id: str) -> str:
    async with async_driver.session() as session:
        record = await session.run("""
            MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)
            RETURN l.name AS name
        """, char_id=char_id).single()
        return record["name"] if record else "알 수 없는 장소"

async def fetch_global_state(fallback_dt: datetime) -> dict:
    """DB의 GlobalState 노드에서 현재 시간, 장소, 날씨를 가져옵니다."""
    async with async_driver.session() as session:
        record = await session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime AS currentTime, 
                   gs.weather AS weather, 
                   gs.currentLocationId AS currentLocationId
        """)
        record = await record.single()
        if record and record.get("currentTime"):
            return dict(record)

        return {
            "currentTime": fallback_dt.isoformat(),
            "weather": "Clear",
            "currentLocationId": "알 수 없는 장소"
        }

def detect_present_npcs(user_input: str, recent_story: str, npc_name_map: dict[str, str]) -> list[str]:
    text = f"{user_input} {recent_story}"
    found: set[str] = set()
    for keyword, char_id in npc_name_map.items():
        if keyword in text:
            found.add(char_id)
    if found:
        print(f"[NPC 감지] {sorted(found)}")
    return sorted(found)


async def fetch_npc_profiles(npc_ids: list[str], main_npc_id: str, pc_id: str) -> list[dict]:
    results = []
    async with async_driver.session() as session:
        for npc_id in npc_ids:
            name_rec = await session.run(
                "MATCH (c:Character {id: $id}) RETURN c.name AS name", id=npc_id
            ).single()
            if not name_rec:
                continue

            profile_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PROFILE]->(p)
                RETURN properties(p) AS props
            """, id=npc_id).single()

            rel_rec = await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                RETURN properties(r) AS props
            """, a=main_npc_id, b=npc_id).single()

            results.append({
                "char_id":    npc_id,
                "name":       name_rec["name"],
                "profile":    profile_rec["props"] if profile_rec else {},
                "rel_to_npc": rel_rec["props"]    if rel_rec    else {},
            })
    return results

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

    world = load_world_instance(world_id)
    world_config = world.get_full_config()
    start_dt = world_config.get("start_time")

    global_state = await fetch_global_state(start_dt)
    scene_types = await classify_scene(user_input, recent_story)

    db_fetch_tasks = [
        fetch_character_data(npc_id, scene_types),
        fetch_character_data(pc_id, scene_types),
        fetch_relationship_data(pc_id, npc_id),
        fetch_recent_events(npc_id, limit=3)
    ]

    db_results: Any = await asyncio.gather(*db_fetch_tasks)
    char_data, user_data, relationship, events = db_results

    current_dt = datetime.fromisoformat(global_state.get("currentTime"))

    loc_id = global_state.get("currentLocationId")
    location_name = await get_location_name_from_id(loc_id) or await fetch_location(npc_id)

    if "dynamic_state" in char_data:
        char_data["dynamic_state"]["location_id"] = location_name

    present_npc_ids = detect_present_npcs(user_input, recent_story, world.get_npc_name_map())
    npcs = await fetch_npc_profiles(present_npc_ids, npc_id, pc_id) if present_npc_ids else []

    builder = PromptBuilder(world_config, char_data.get("name"), user_data.get("name"))

    fixed_prompt, genre_prompt, dynamic_prompt = builder.build(
        scene_types=scene_types,
        char_data=char_data,
        relationship=relationship,
        events=[
            {"timestamp": e["timestamp"], "summary": e["summary"]}
            for e in events
        ],
        recent_story=recent_story,
        user_input=user_input,
        location=location_name,
        npcs=npcs,
        dt=current_dt,
    )

    return fixed_prompt, genre_prompt, dynamic_prompt, scene_types

async def get_location_name_from_id(location_id: str | None) -> str | None:
    """주어진 ID로 Location 노드의 이름을 찾습니다."""
    if not location_id:
        return None
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (l:Location {id: $id}) RETURN l.name AS name",
            id=location_id
        )
        record = await result.single()
        return record["name"] if record else None

if __name__ == "__main__":
    fixed, genre, dynamic, scene_types = run_manager(
        user_input="*지희와 아린이 놀러 왔다. 은서와 셋이 소파에 앉아 수다를 떤다.*",
        pc_id="sian", npc_id="eun_seo",
        recent_story="토요일 오후, 은서네 집.",
        world_id="babe_univ"
    )
    print("=== FIXED ==="); print(fixed[:200], "...\n")
    print("=== GENRE ==="); print(genre[:200] if genre else "(없음)", "\n")
    print("=== DYNAMIC ==="); print(dynamic)
    print("\n=== 씬 타입 ==="); print(scene_types)