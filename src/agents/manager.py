# ================================
# src/agents/manager.py
#
# 씬 분류, 시간 계산, 프롬프트 조립을 오케스트레이션합니다.
#
# Functions
#   - load_world_instance(world_id: str) -> World : world_id에 해당하는 World 인스턴스 로드
#   - run_manager(user_input, pc_id, npc_id, recent_story, world_id, perspective) -> tuple[str, str, str, list[str]] : 전체 파이프라인 실행 후 (fixed, genre, dynamic, scene_types) 반환
#   - commit_manager_effects(effects: dict, pc_id: str, npc_id: str) -> None : pending manager side effect를 확정 반영
#   - _classify_and_parse_time(user_input, recent_story, global_state, allowed_locs, scene_descriptions) -> dict : 씬 분류 + 시간 파싱 (scene_descriptions는 세계관별 타입명→설명 dict)
# ================================

import asyncio
import json
import re
from datetime import datetime
from importlib import import_module

from src.config import MODEL_CLASSIFIER as CLASSIFIER_MODEL
from src.agents.manager_pipeline import ManagerDependencies, run_manager_pipeline
from src.assets.worlds.base import World
from src.core.database.driver import async_driver
from src.core.llm.client import extract_json_from_llm, get_model
from src.simulation.systems.memory import run_decay
from src.simulation.systems.organic import tick_cycle_day
from src.simulation.state.updater import commit_time_plan
from src.simulation.systems.needs import run_needs_update
from src.simulation.events import evaluate_all as evaluate_static_events

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

_NEEDS_LLM_PATTERN = re.compile(
    r"\*|다음\s*날|내일|어제|시간\s*후|분\s*후|나중에|며칠|다음\s*주|"
    r"이동|장소|헬스장|카페|학교|편의점|집에|나갔|들어왔|"
    r"날씨|비|눈|천둥|intimate|직장|workplace"
)
_RULE_BASED_MAX_LEN = 60


# ════════════════════════════════════════════════════════════
# 씬 분류 + 시간 계산 (통합)
# ════════════════════════════════════════════════════════════

def _try_rule_based(user_input: str) -> dict | None:
    if len(user_input) > _RULE_BASED_MAX_LEN:
        return None
    if _NEEDS_LLM_PATTERN.search(user_input):
        return None
    return {
        "scene_types":     ["daily"],
        "action_type":     "dialogue",
        "elapsed_minutes": 2,
        "new_weather":     None,
        "new_location_id": None,
        "reason":          "rule-based: short dialogue",
    }


async def _classify_and_parse_time(
    user_input:        str,
    recent_story:      str,
    global_state:      dict,
    allowed_locs:      str,
    scene_descriptions: dict[str, str] | None = None,
) -> dict:
    current_time    = datetime.fromisoformat(global_state["currentTime"])
    context_snippet = recent_story[-800:] if recent_story else ""
    _scenes = scene_descriptions or {"daily": "Everyday life with no significant conflict"}
    scene_types_block = "\n".join(f"  - {name}: {desc}" for name, desc in _scenes.items())

    system_instruction = "You are a combined scene classifier and time parser for a Korean roleplay system."

    prompt = f"""Analyze the user input and return a single JSON object. No explanation, no markdown.
Return ONLY valid JSON. No markdown fences. No ellipsis. No truncation.
If a field is uncertain, use null — never use "...".

[Current World State]
Time: {current_time.strftime("%Y-%m-%d %H:%M")} | Weather: {global_state["weather"]} | Location: {global_state["currentLocationId"]}

[Allowed Locations]
{allowed_locs}

[Context]
{context_snippet}

[User Input]
{user_input}

[Rules]
scene_types: pick 1+ from the list below (use exact keys):
{scene_types_block}
action_type: "dialogue"(3min) | "action"(10min) | "movement"(25min) | "ooc_jump"(null min, use target_hour)
target_hour: int (0-23) only for ooc_jump. Map: 새벽→3, 아침→8, 점심→12, 오후→15, 저녁→19, 밤→23
new_location_id: existing ID from Allowed Locations ONLY, else null
new_weather: from [Clear,Cloudy,Foggy,Drizzle,Rain,Heavy Rain,Thunderstorm,Snow,Heavy Snow,Windy] or null

[Output — ONLY this JSON]
{{
  "scene_types": [...],
  "action_type": "...",
  "target_hour": null,
  "elapsed_minutes": 3,
  "new_weather": null,
  "new_location_id": null,
  "reason": "..."
}}
"""

    try:
        model = get_model(model_name=CLASSIFIER_MODEL, system_prompt=system_instruction)

        resp = model.generate_content(
            prompt,
            generation_config={
                "max_output_tokens": 256,
                "temperature": 0.0,
                "thinking_config": {"thinking_level": "LOW"},
            }
        )
        raw    = resp.text
        parsed = extract_json_from_llm(raw, source="manager_agent")
        if not isinstance(parsed, dict) or "scene_types" not in parsed:
            raise ValueError("invalid structure")
        print(f"[Classify+Time / {CLASSIFIER_MODEL}] scene={parsed.get('scene_types')} elapsed={parsed.get('elapsed_minutes')}min")
        return parsed
    except Exception as e:
        print(f"[Classify+Time 실패] {e} → fallback")
        return {
            "scene_types":     ["daily"],
            "action_type":     "dialogue",
            "elapsed_minutes": 3,
            "new_weather":     None,
            "new_location_id": None,
        }


# ════════════════════════════════════════════════════════════
# 세계 인스턴스 로드
# ════════════════════════════════════════════════════════════

def load_world_instance(world_id: str) -> World:
    try:
        module = import_module(f"src.assets.worlds.{world_id}.schema")
        # 모듈에 미리 만들어진 world_instance가 있으면 그걸 쓴다.
        # RoFanNorthGenderbendWorld처럼 __init__에 필수 인수가 있는 경우를 처리.
        if isinstance(getattr(module, "world_instance", None), World):
            return module.world_instance
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
                return obj()
    except Exception as e:
        print(f"[WorldLoader] {world_id} 로드 실패: {e}")
    return World()


# ════════════════════════════════════════════════════════════
# DB 조회
# ════════════════════════════════════════════════════════════

async def fetch_character_data(char_id: str, scene_types: list[str]) -> dict:
    needed_rels: set[str] = {"HAS_PROFILE", "HAS_PERSONALITY", "HAS_STATE"}
    for st in scene_types:
        needed_rels.update(SCENE_REL_MAP.get(st, []))

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
            RETURN e.id           AS id,
                   e.summary      AS summary,
                   e.timestamp    AS timestamp,
                   e.impact       AS impact,
                   m_npc.summary  AS npc_memory,
                   m_pc.summary   AS pc_memory
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


async def fetch_npc_profiles(
    npc_ids:     list[str],
    main_npc_id: str,
    pc_id:       str,
) -> list[dict]:
    results = []
    async with async_driver.session() as session:
        for nid in npc_ids:
            if nid in (main_npc_id, pc_id):
                continue
            name_rec = await session.run(
                "MATCH (c:Character {id: $id}) RETURN c.name AS name", id=nid
            )
            name_row = await name_rec.single()
            if not name_row:
                continue

            profile_rec = await session.run("""
                MATCH (c:Character {id: $id})-[:HAS_PROFILE]->(p)
                RETURN p AS props
            """, id=nid)
            profile_row = await profile_rec.single()

            rel_rec = await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                RETURN r AS props
            """, a=main_npc_id, b=nid)
            rel_row = await rel_rec.single()

            raw_profile = profile_row["props"] if profile_row else {}
            if isinstance(raw_profile, dict) and isinstance(raw_profile.get("props"), str):
                try:
                    raw_profile = json.loads(raw_profile["props"])
                except (ValueError, TypeError):
                    pass
            results.append({
                "char_id":    nid,
                "name":       name_row["name"],
                "profile":    raw_profile,
                "rel_to_npc": rel_row["props"] if rel_row else {},
            })
    return results


async def commit_manager_effects(effects: dict | None, pc_id: str, npc_id: str) -> None:
    """Actor 응답이 확정된 턴의 manager side effect를 DB에 반영합니다."""
    if not effects:
        return

    time_plan = effects.get("time_plan")
    current_dt: datetime | None = None
    if time_plan:
        current_dt = await commit_time_plan(time_plan, pc_id, npc_id)

    needs_plan = effects.get("needs_update") or {}
    if needs_plan:
        try:
            needs_time = current_dt or datetime.fromisoformat(needs_plan["current_time"])
            await run_needs_update(
                pc_id           = needs_plan.get("pc_id") or pc_id,
                elapsed_minutes = float(needs_plan.get("elapsed_minutes") or 1.0),
                current_time    = needs_time,
            )
        except Exception as e:
            print(f"[ManagerCommit] needs update 실패 (무시): {e}")

    daily_plan = effects.get("daily_systems") or {}
    days_passed = int(daily_plan.get("days_passed") or 0)
    if days_passed > 0:
        daily_time = current_dt or datetime.fromisoformat(daily_plan["current_time"])
        try:
            await run_decay(daily_time)
        except Exception as e:
            print(f"[ManagerCommit] decay 실패 (무시): {e}")
        try:
            await tick_cycle_day(npc_id, days_passed)
        except Exception as e:
            print(f"[ManagerCommit] cycle tick 실패 (무시): {e}")

    if current_dt:
        try:
            await evaluate_static_events(current_dt, commit=True)
        except Exception as e:
            print(f"[ManagerCommit] StaticEvent 평가 실패 (무시): {e}")


# ════════════════════════════════════════════════════════════
# 메인 파이프라인
# ════════════════════════════════════════════════════════════

async def run_manager(
    user_input:   str,
    pc_id:        str,
    npc_id:       str,
    recent_story: str = "",
    world_id:     str = None,
    perspective:  int = 3,
    return_meta:   bool = False,
) -> tuple[str, str, str, list[str]] | tuple[str, str, str, list[str], dict]:
    """Orchestrate turn preparation while leaving each stage testable in isolation."""
    prompts, scene_types, manager_effects = await run_manager_pipeline(
        user_input,
        pc_id,
        npc_id,
        recent_story,
        world_id,
        perspective,
        ManagerDependencies(
            load_world_instance=load_world_instance,
            fetch_global_state=fetch_global_state,
            try_rule_based=_try_rule_based,
            get_allowed_locations=_get_allowed_locations,
            classify_and_parse_time=_classify_and_parse_time,
            fetch_character_data=fetch_character_data,
            fetch_relationship_data=fetch_relationship_data,
            fetch_recent_events=fetch_recent_events,
            get_location_name_from_id=get_location_name_from_id,
            fetch_location=fetch_location,
            detect_present_npcs=detect_present_npcs,
            fetch_npc_profiles=fetch_npc_profiles,
        ),
    )

    if return_meta:
        return (
            prompts.fixed,
            prompts.genre,
            prompts.dynamic,
            scene_types,
            manager_effects,
        )
    return prompts.fixed, prompts.genre, prompts.dynamic, scene_types

# ════════════════════════════════════════════════════════════
# 테스트
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def _test():
        fixed, genre, dynamic, scene_types = await run_manager(
            user_input   = "*지희와 아린이 놀러 왔다. 은서와 셋이 소파에 앉아 수다를 떤다.*",
            pc_id        = "sian",
            npc_id       = "eun_seo",
            recent_story = "토요일 오후, 은서네 집.",
            world_id     = "babe_univ",
            perspective  = 3,
        )
        print("=== FIXED ===");   print(fixed[:200],  "...\n")
        print("=== GENRE ===");   print(genre[:200] if genre else "(없음)", "\n")
        print("=== DYNAMIC ==="); print(dynamic)
        print("\n=== 씬 타입 ==="); print(scene_types)

    asyncio.run(_test())
