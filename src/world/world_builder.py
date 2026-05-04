"""
이야기 중 처음 등장하는 캐릭터를 자동으로 그래프에 추가하는 에이전트.

흐름:
  Sonnet CoT의 CHARACTERS 파싱 결과 →
    resolve_and_update(char_names) →
      known: 등장 횟수 증가 + 승격 체크
      unknown: stub 생성 (Haiku 1회)

Haiku 호출 조건:
  - 처음 보는 이름일 때만 stub 생성 (1회)
  - 승격 시 Personality 생성 (드물게)
  → 이름 추출 자체엔 LLM 사용 안 함

캐릭터 타입:
  transient: 간단한 stub. libido_excluded=true. 욕구 시스템 미포함.
  named:     풀 스키마. 승격 후 욕구 시스템 편입.

승격 기준 (하나라도 충족):
  - appearance_count >= 3
  - 관련 Event importance >= 5
  - 메인 NPC와 affinity 절댓값 >= 40
"""

import os
import re
from datetime import datetime

from src.utils.db_utils import async_driver
from src.utils.llm_utils import get_model, extract_json_from_llm
from src.needs.traits_initializer import ensure_traits

BUILDER_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

PROMOTE_APPEARANCE_COUNT = 3
PROMOTE_IMPORTANCE       = 5
PROMOTE_AFFINITY_ABS     = 40

_known_chars_cache: dict[str, str] | None = None


# ════════════════════════════════════════════════════════════
# 퍼블릭 진입점
# ════════════════════════════════════════════════════════════

async def resolve_and_update(
    char_names:       list[str],
    main_npc_id:      str,
    pc_id:            str,
    world_config:     dict,
    event_id:         str | None = None,
    event_importance: int = 0,
) -> list[str]:
    """
    CoT에서 파싱한 등장인물 이름 목록을 받아 처리.
    Returns: 이번 턴에 새로 생성된 char_id 목록
    """
    if not char_names:
        return []

    known       = await _get_known_chars()
    created_ids: list[str] = []

    for name in char_names:
        char_id = _resolve_identity(name, known)

        if char_id:
            if event_id:
                await _link_to_event(char_id, event_id)
            await _increment_appearance(char_id)
            await _check_and_promote(char_id, main_npc_id, event_importance)
        else:
            char_id = await _create_stub(
                name_kor     = name,
                main_npc_id  = main_npc_id,
                pc_id        = pc_id,
                world_config = world_config,
            )
            if char_id:
                created_ids.append(char_id)
                _invalidate_cache()
                if event_id:
                    await _link_to_event(char_id, event_id)
                await _increment_appearance(char_id)

    return created_ids


# ════════════════════════════════════════════════════════════
# Identity resolution + 캐시
# ════════════════════════════════════════════════════════════

async def _get_known_chars() -> dict[str, str]:
    global _known_chars_cache
    if _known_chars_cache is not None:
        return _known_chars_cache

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name, c.aliases AS aliases
        """)
        rows = await rec.data()

    result: dict[str, str] = {}
    for r in rows:
        if r["name"]:
            result[r["name"]] = r["id"]
        for alias in (r["aliases"] or []):
            result[alias] = r["id"]

    _known_chars_cache = result
    return result


def _invalidate_cache() -> None:
    global _known_chars_cache
    _known_chars_cache = None


def _resolve_identity(name: str, known: dict[str, str]) -> str | None:
    if name in known:
        return known[name]
    if len(name) >= 2:
        for k, v in known.items():
            if name in k or k in name:
                return v
    return None


# ════════════════════════════════════════════════════════════
# Stub 생성
# ════════════════════════════════════════════════════════════

def _kor_to_roman_id(name_kor: str) -> str:
    ts   = datetime.now().strftime("%m%d%H%M")
    safe = re.sub(r'[^a-z0-9]', '', name_kor.encode('ascii', 'ignore').decode())
    if not safe:
        safe = "npc"
    return f"{safe}_{ts}"


async def _create_stub(
    name_kor:    str,
    main_npc_id: str,
    pc_id:       str,
    world_config: dict,
) -> str | None:
    """Transient NPC stub 생성. char_id 반환."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {name: $name}) RETURN c.id AS id", name=name_kor
        )
        row = await rec.single()
        if row:
            return row["id"]

    char_id   = _kor_to_roman_id(name_kor)
    stub      = await _generate_stub_profile(name_kor, world_config, main_npc_id)
    if not stub:
        stub = {
            "personality":    "unknown",
            "context":        "",
            "relation_type":  "acquaintance",
            "relation_status": "first encounter",
            "initial_affinity": 0,
        }

    timestamp = datetime.now().isoformat()

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Character {id: $id, name: $name, type: "transient"})
        """, id=char_id, name=name_kor)

        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                id:               $pid,
                name_kor:         $name_kor,
                type:             "transient",
                personality:      $personality,
                context:          $context,
                role:             $role,
                first_seen:       $ts,
                last_seen:        $ts,
                appearance_count: 0,
                libido_excluded:  true
            })
        """,
            cid         = char_id,
            pid         = f"{char_id}_static",
            name_kor    = name_kor,
            personality = stub.get("personality", ""),
            context     = stub.get("context", ""),
            role        = stub.get("relation_type", "acquaintance"),
            ts          = timestamp,
        )

        await session.run("""
            MATCH (a:Character {id: $main}), (b:Character {id: $cid})
            CREATE (a)-[:RELATIONSHIP {
                type:           $rel_type,
                affinity:       $affinity,
                trust:          10,
                current_status: $status
            }]->(b)
        """,
            main     = main_npc_id,
            cid      = char_id,
            rel_type = stub.get("relation_type", "acquaintance"),
            affinity = int(stub.get("initial_affinity", 0)),
            status   = stub.get("relation_status", "first encounter"),
        )

    print(f"[WorldBuilder] Transient 생성: {name_kor} ({char_id})")
    return char_id


async def _generate_stub_profile(
    name_kor:    str,
    world_config: dict,
    main_npc_id: str,
) -> dict | list | None:
    """Haiku 1회 — stub 프로필 초안 생성."""
    world_ctx = world_config.get("world_section", "")[:300]

    system_instruction = "Generate a minimal character stub for a new NPC in a Korean slice-of-life roleplay."

    prompt = f"""World: {world_ctx}
Main NPC: {main_npc_id}
New character name: {name_kor}

Return ONLY JSON:
{{
  "personality":       "2-3 adjective keywords, English, plus-separated",
  "context":           "1 sentence: who they are (Korean OK)",
  "relation_type":     "acquaintance / classmate / coworker / customer / stranger",
  "relation_status":   "1 sentence: current relation to main NPC (English)",
  "initial_affinity":  0
}}"""

    try:
        model = get_model(BUILDER_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={}
        )
        return extract_json_from_llm(resp.text)
    except Exception as e:
        print(f"[WorldBuilder] stub 생성 실패: {e}")
        return None



# ════════════════════════════════════════════════════════════
# 등장 횟수 + 승격
# ════════════════════════════════════════════════════════════

async def _increment_appearance(char_id: str) -> None:
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            SET sp.appearance_count = coalesce(sp.appearance_count, 0) + 1,
                sp.last_seen        = $ts
        """, cid=char_id, ts=datetime.now().isoformat())


async def _link_to_event(char_id: str, event_id: str) -> None:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event {id: $eid})
            RETURN e.id AS id
        """, cid=char_id, eid=event_id)
        if await rec.single():
            return
        await session.run("""
            MATCH (c:Character {id: $cid}), (e:Event {id: $eid})
            CREATE (c)-[:INVOLVED_IN]->(e)
        """, cid=char_id, eid=event_id)


async def _check_and_promote(
    char_id:          str,
    main_npc_id:      str,
    event_importance: int,
) -> None:
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.appearance_count AS count, c.type AS type
        """, cid=char_id)
        row = await rec.single()
        if not row or row["type"] != "transient":
            return
        count = row["count"] or 0

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
        _invalidate_cache()


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
            RETURN properties(sp) AS props
        """, cid=char_id)
        row     = await rec2.single()
        profile = dict(row["props"]) if row else {}

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
            generation_config={}
        )
        parsed = extract_json_from_llm(resp.text)
        if isinstance(parsed, dict):
            personality_data = parsed
    except Exception as e:
        print(f"[WorldBuilder] Personality 생성 실패: {e}")

    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PERSONALITY]->(:Personality {
                id:                  $pid,
                core_traits:         $traits,
                speech_style:        $speech,
                habit_when_thinking: $habit,
                sample_line:         $sample
            })
        """,
            cid    = char_id,
            pid    = f"{char_id}_personality",
            traits = personality_data.get("core_traits", ""),
            speech = personality_data.get("speech_style", ""),
            habit  = personality_data.get("habit_when_thinking", ""),
            sample = personality_data.get("sample_line", ""),
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

        await session.run("""
            MATCH (c:Character {id: $cid})
            SET c.type = "named"
            WITH c
            MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            SET sp.type = "named"
        """, cid=char_id)

    try:
        await ensure_traits(char_id)
    except Exception as e:
        print(f"[WorldBuilder] traits 생성 실패: {e}")

    print(f"[WorldBuilder] ★ Named NPC 승격: {char_id}")