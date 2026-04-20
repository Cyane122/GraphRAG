# src/agents/manager_agent.py

from openai import OpenAI
from neo4j import GraphDatabase
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime
from src.prompt.PromptBuilder import PromptBuilder
import os, json

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# ── 클라이언트 초기화 ────────────────────────────────────
llm = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

CLASSIFIER_MODEL = "google/gemma-3-12b-it:free" # "meta-llama/llama-3.3-70b-instruct:free"
builder = PromptBuilder()


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

    response = llm.chat.completions.create(
        model=CLASSIFIER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )

    raw = response.choices[0].message.content.strip()

    # JSON 파싱 실패 시 폴백
    try:
        # 혹시 ```json ... ``` 감싸진 경우 제거
        clean = raw.replace("```json", "").replace("```", "").strip()
        scene_types = json.loads(clean)
        if not isinstance(scene_types, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        print(f"[분류 파싱 실패] raw: {raw} → 폴백: ['daily']")
        scene_types = ["daily"]

    print(f"[씬 분류] {scene_types}")
    return scene_types


# ════════════════════════════════════════════════════════════
# 2~3단계: Graph 데이터 추출
# ════════════════════════════════════════════════════════════

# 씬 타입 → 필요한 서브 노드 매핑
SCENE_NODE_MAP = {
    "daily":     ["HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"],
    "emotional": ["HAS_PERSONALITY", "HAS_STATE"],
    "physical":  ["HAS_PROFILE", "HAS_STATE"],
    "intimate":  ["HAS_PROFILE", "HAS_STATE", "HAS_INTIMATE"],
    "workplace": ["HAS_PROFILE", "HAS_STATE", "HAS_WORKPLACE"],
    "aegyo":     ["HAS_PERSONALITY", "HAS_STATE"],
}

# 릴레이션십 타입 → char_data 키 매핑
REL_TO_KEY = {
    "HAS_PROFILE":    "static_profile",
    "HAS_PERSONALITY": "personality",
    "HAS_STATE":      "dynamic_state",
    "HAS_INTIMATE":   "intimate_profile",
    "HAS_WORKPLACE":  "workplace_profile",
}

def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    # 씬 타입들에 필요한 릴레이션십 타입 합산 (중복 제거)
    needed_rels = set()
    for st in scene_types:
        needed_rels.update(SCENE_NODE_MAP.get(st, []))

    result = {}
    with driver.session() as session:
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


# ════════════════════════════════════════════════════════════
# 4단계: 비동기 DB 업데이트 (Actor 응답 후 호출)
# ════════════════════════════════════════════════════════════

def update_dynamic_state(char_id: str, updates: dict) -> None:
    """
    DynamicState 노드 속성 업데이트.
    updates 예시: {"mood": "happy", "workplace_stress_level": 3}
    """
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
    """새 이벤트 노드 생성 + 캐릭터 연결 + RELATIONSHIP shared_events 업데이트"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    with driver.session() as session:
        session.run("""
            CREATE (:Event {
                id: $event_id,
                summary: $summary,
                timestamp: $timestamp,
                location_id: $location_id,
                impact: $impact
            })
        """, event_id=event_id, summary=summary,
             timestamp=timestamp, location_id=location_id, impact=impact)

        for char_id in char_ids:
            session.run("""
                MATCH (c:Character {id: $char_id}), (e:Event {id: $event_id})
                CREATE (c)-[:INVOLVED_IN]->(e)
            """, char_id=char_id, event_id=event_id)

        # 두 캐릭터 간 RELATIONSHIP shared_events 업데이트
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
    dt: datetime = None,
) -> tuple[str, str, list[str]]:
    """
    Returns:
        fixed_prompt  : 캐시 대상 고정 파트
        dynamic_prompt: 매 턴 교체 동적 파트
        scene_types   : 분류된 씬 타입 (DB 업데이트 등에 활용)
    """

    # 1. 씬 분류
    scene_types = classify_scene(user_input, recent_story)

    # 2. 데이터 추출
    char_data    = fetch_character_data(npc_id, scene_types)
    relationship = fetch_relationship_data(pc_id, npc_id)
    events       = fetch_recent_events(npc_id, limit=3)
    location     = fetch_location(npc_id)

    # 3. 프롬프트 조립
    fixed_prompt, dynamic_prompt = builder.build(
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
        dt=dt or datetime.now(),
    )

    return fixed_prompt, dynamic_prompt, scene_types


# ── 테스트 ────────────────────────────────────────────────
if __name__ == "__main__":
    fixed, dynamic, scene_types = run_manager(
        user_input="*은서와 함께 욕실에 들어간다. 은서는 헤헤 웃으며 옷을 벗는다.*",
        pc_id="sian",
        npc_id="eun_seo",
        recent_story="점심 무렵까지 은서와 시안이 침대에서 뒹굴다가 씻기 위해 일어났다.",
    )

    print("=== FIXED ===")
    print(fixed[:200], "...\n")
    print("=== DYNAMIC ===")
    print(dynamic)
    print("\n=== 씬 타입 ===")
    print(scene_types)