# src/agents/manager_agent.py

import os
import re
from openai import OpenAI
from neo4j import GraphDatabase
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime
from src.prompt.PromptBuilder import PromptBuilder
import json
from importlib import import_module
from src.graph.world.default import World

load_dotenv(Path(__file__).parent.parent.parent / ".env")

llm = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

CLASSIFIER_MODEL    = os.getenv("MODEL_CLASSIFIER",          "google/gemma-3-12b-it:free")
CLASSIFIER_FALLBACK = os.getenv("MODEL_CLASSIFIER_FALLBACK", "meta-llama/llama-3.3-70b-instruct:free")


# ════════════════════════════════════════════════════════════
# 1단계: 씬 분류
# ════════════════════════════════════════════════════════════

def classify_scene(user_input: str, recent_story: str) -> list[str]:
    prompt = f"""You are a scene classifier for a roleplay system.
Analyze the user input and recent story, return a JSON array of scene types.

Possible types: daily / emotional / physical / intimate / workplace / aegyo
Multiple types allowed. Return ONLY a JSON array. No explanation, no markdown.
Example: ["daily", "emotional"]

Recent story:
{recent_story}

User input:
{user_input}"""

    for model in [CLASSIFIER_MODEL, CLASSIFIER_FALLBACK]:
        try:
            response = llm.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            raw   = response.choices[0].message.content.strip()
            clean = raw.replace("```json", "").replace("```", "").strip()
            scene_types = json.loads(clean)
            if not isinstance(scene_types, list):
                raise ValueError("not a list")
            print(f"[씬 분류 / {model}] {scene_types}")
            return scene_types
        except Exception as e:
            print(f"[씬 분류 실패 / {model}] {e}")

    print("[씬 분류] 모든 모델 실패 → 폴백: ['daily']")
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

def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    needed_rels = set()
    for st in scene_types:
        needed_rels.update(SCENE_NODE_MAP.get(st, []))

    result = {}
    with driver.session() as session:
        # ── 1. 허브 노드(:Character) 자체의 속성(name 등) 가져오기 ──
        hub_record = session.run("""
            MATCH (c:Character {id: $char_id})
            RETURN properties(c) AS props
        """, char_id=char_id).single()

        if hub_record:
            # result의 최상위 레벨에 name, id 등의 기본 정보를 먼저 넣습니다.
            result.update(hub_record["props"])
        else:
            result["name"] = char_id

        # ── 2. 서브 노드 데이터 가져오기 (기존 로직) ──
        for rel_type in needed_rels:
            records = session.run(f"""
                MATCH (c:Character {{id: $char_id}})-[:{rel_type}]->(n)
                RETURN properties(n) AS props
            """, char_id=char_id)
            record = records.single()
            if record:
                key = REL_TO_KEY.get(rel_type, rel_type.lower())
                result[key] = record["props"]
    return result


def fetch_relationship_data(char_a: str, char_b: str) -> dict:
    with driver.session() as session:
        record = session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN properties(r) AS props
        """, a=char_a, b=char_b).single()
        return record["props"] if record else {}


def fetch_recent_events(char_id: str, limit: int = 3) -> list[dict]:
    with driver.session() as session:
        records = session.run("""
            MATCH (c:Character {id: $char_id})-[:INVOLVED_IN]->(e:Event)
            RETURN e.id AS id, e.summary AS summary,
                   e.timestamp AS timestamp, e.impact AS impact
            ORDER BY e.timestamp DESC
            LIMIT $limit
        """, char_id=char_id, limit=limit)
        return [dict(r) for r in records]


def fetch_location(char_id: str) -> str:
    with driver.session() as session:
        record = session.run("""
            MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)
            RETURN l.name AS name
        """, char_id=char_id).single()
        return record["name"] if record else "알 수 없는 장소"

def fetch_global_state() -> dict:
    """DB의 GlobalState 노드에서 현재 시간, 장소, 날씨를 가져옵니다."""
    with driver.session() as session:
        record = session.run("""
            MATCH (gs:GlobalState {id: 'singleton'})
            RETURN gs.currentTime AS currentTime, 
                   gs.weather AS weather, 
                   gs.currentLocationId AS currentLocationId
        """).single()
        if record:
            return dict(record)
        return {
            "currentTime": datetime.now().isoformat(),
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


def fetch_npc_profiles(npc_ids: list[str], main_npc_id: str, pc_id: str) -> list[dict]:
    results = []
    with driver.session() as session:
        for npc_id in npc_ids:
            name_rec = session.run(
                "MATCH (c:Character {id: $id}) RETURN c.name AS name", id=npc_id
            ).single()
            if not name_rec:
                continue

            profile_rec = session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PROFILE]->(p)
                RETURN properties(p) AS props
            """, id=npc_id).single()

            rel_rec = session.run("""
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
# 4단계: 비동기 DB 업데이트
# ════════════════════════════════════════════════════════════

def update_dynamic_state(char_id: str, updates: dict) -> None:
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    with driver.session() as session:
        session.run(f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            SET {set_clause}
        """, char_id=char_id, **updates)
    print(f"[DB 업데이트] {char_id} → {updates}")


def append_event(event_id: str, summary: str, location_id: str,
                 impact: str, char_ids: list[str]) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    with driver.session() as session:
        session.run("""
            CREATE (:Event {
                id: $event_id, summary: $summary, timestamp: $timestamp,
                location_id: $location_id, impact: $impact
            })
        """, event_id=event_id, summary=summary,
             timestamp=timestamp, location_id=location_id, impact=impact)

        for char_id in char_ids:
            session.run("""
                MATCH (c:Character {id: $char_id}), (e:Event {id: $event_id})
                CREATE (c)-[:INVOLVED_IN]->(e)
            """, char_id=char_id, event_id=event_id)

        if len(char_ids) == 2:
            session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                SET r.shared_events = r.shared_events + [$event_id],
                    r.last_interaction = $timestamp
            """, a=char_ids[0], b=char_ids[1],
                 event_id=event_id, timestamp=timestamp)

    print(f"[이벤트 추가] {event_id}")


# ════════════════════════════════════════════════════════════
# 메인 파이프라인
# ════════════════════════════════════════════════════════════

def run_manager(
    user_input: str,
    pc_id: str,
    npc_id: str,
    recent_story: str = "",
    world_id: str = None,
    previous_ai_response: str = None,
) -> tuple[str, str, str, list[str]]:

    if previous_ai_response:
        # TODO: 사용자의 input이 들어왔으므로 input 기반 상태 갱신 구현 필요
        pass

    world = load_world_instance(world_id)
    world_config = world.get_full_config()
    global_state = fetch_global_state()

    scene_types = classify_scene(user_input, recent_story)

    char_data    = fetch_character_data(npc_id, scene_types)
    user_data    = fetch_character_data(pc_id, scene_types)
    relationship = fetch_relationship_data(pc_id, npc_id)
    events       = fetch_recent_events(npc_id, limit=3)
    current_dt   = datetime.fromisoformat(global_state["currentTime"])
    location = global_state.get("currentLocationId", fetch_location(npc_id))

    present_npc_ids = detect_present_npcs(user_input, recent_story, world.get_npc_name_map())
    npcs = fetch_npc_profiles(present_npc_ids, npc_id, pc_id) if present_npc_ids else []

    builder = PromptBuilder(world_config, char_data.get("name"), user_data.get("name"))

    fixed_prompt, genre_prompt, dynamic_prompt = builder.build(
        scene_types=scene_types,
        char_data=char_data,
        relationship=relationship,
        events=[
            {"e.timestamp": e["timestamp"], "e.summary": e["summary"]}
            for e in events
        ],
        recent_story=recent_story,
        user_input=user_input,
        location=location,
        dt=current_dt,
        npcs=npcs,
    )

    return fixed_prompt, genre_prompt, dynamic_prompt, scene_types


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