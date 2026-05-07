# ================================
# src/simulation/systems/social.py
#
# NPC 자율행동 세계 맥락 생성 및 캐릭터 그래프 관리를 담당합니다.
#
# Functions
#   - build_world_context(npc_id, pc_id, location_id, current_time) -> dict : 근처 활동 + SNS 피드 생성
#   - resolve_and_update(char_names, main_npc_id, pc_id, world_config, event_id, event_importance) -> list[str] : 등장인물 이름 처리 및 그래프 갱신
# ================================

import json
import re
from datetime import datetime, timedelta

from src.config import MODEL_STATE_UPDATER
from src.core.database.driver import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
from src.simulation.systems.needs import ensure_traits

NARRATOR_MODEL = MODEL_STATE_UPDATER
BUILDER_MODEL  = MODEL_STATE_UPDATER
NEARBY_WINDOW_HOURS = 2
WORLD_WINDOW_HOURS  = 8
MAX_SNS_POSTS       = 2
MAX_NEARBY          = 3

PROMOTE_APPEARANCE_COUNT = 3
PROMOTE_IMPORTANCE       = 5
PROMOTE_AFFINITY_ABS     = 40

_known_chars_cache: dict[str, str] | None = None


# ════════════════════════════════════════════════════════════
# 세계 맥락 생성 (구 world_narrator.py)
# ════════════════════════════════════════════════════════════

async def build_world_context(
    npc_id:       str,
    pc_id:        str,
    location_id:  str,
    current_time: datetime,
) -> dict:
    """
    manager_agent에서 위치 확정 직후 호출.

    Returns:
        {
            "nearby_activity": [{"name": str, "summary": str}],
            "sns_posts":       [str],
        }
    """
    events = await _fetch_recent_auto_events(npc_id, pc_id, current_time, WORLD_WINDOW_HOURS)
    if not events:
        return {"nearby_activity": [], "sns_posts": []}

    cutoff_nearby = current_time - timedelta(hours=NEARBY_WINDOW_HOURS)
    nearby = [
        {"name": e["char_name"], "summary": e["summary"]}
        for e in events
        if e.get("location_id") == location_id
        and _parse_ts(e["timestamp"]) >= cutoff_nearby
    ]

    sns_candidates = [
        e for e in events
        if e.get("need_name") in ("social", "fun")
        and e.get("sns_handle")
    ]

    sns_posts: list[str] = []
    if sns_candidates:
        sns_posts = await _generate_sns_batch(sns_candidates[:MAX_SNS_POSTS])

    print(
        f"[WorldNarrator] nearby={len(nearby[:MAX_NEARBY])} "
        f"sns={len(sns_posts)}"
    )

    return {
        "nearby_activity": nearby[:MAX_NEARBY],
        "sns_posts":       sns_posts,
    }


async def _fetch_recent_auto_events(
    npc_id:       str,
    pc_id:        str,
    current_time: datetime,
    window_hours: int,
) -> list[dict]:
    """최근 자율행동 Events + 캐릭터 StaticProfile 조인. npc_id/pc_id 본인 이벤트 제외."""
    cutoff = (current_time - timedelta(hours=window_hours)).isoformat()

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:INVOLVED_IN]->(e:Event)
            WHERE c.id <> $npc_id
              AND c.id <> $pc_id
              AND e.impact = "autonomous need resolution"
              AND e.timestamp >= $cutoff
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN c.id          AS char_id,
                   c.name        AS char_name,
                   e.id          AS event_id,
                   e.summary     AS summary,
                   e.timestamp   AS timestamp,
                   e.location_id AS location_id,
                   e.need_name   AS need_name,
                   sp.props      AS profile_props
            ORDER BY e.timestamp DESC
            LIMIT 15
        """, npc_id=npc_id, pc_id=pc_id, cutoff=cutoff)
        rows = await rec.data()

    result = []
    for r in rows:
        row = dict(r)
        profile_props = row.pop("profile_props", None) or "{}"
        try:
            profile = json.loads(profile_props) if isinstance(profile_props, str) else (profile_props or {})
            row["sns_handle"] = profile.get("sns_handle")
        except Exception:
            row["sns_handle"] = None
        result.append(row)
    return result


async def _generate_sns_batch(candidates: list[dict]) -> list[str]:
    """Haiku에 SNS 게시글 생성 배치 요청 (1회 호출)."""
    items = [
        {
            "id":         c["event_id"],
            "char_name":  c["char_name"],
            "sns_handle": c["sns_handle"],
            "action":     c["summary"],
        }
        for c in candidates
    ]

    system_instruction = "You are generating realistic Korean SNS posts for fictional characters in a slice-of-life roleplay."

    prompt = f"""Each character just did something. Write a short post they might upload to their feed.

Rules:
- 1–2 lines max, casual Korean
- Match the character's personality if inferable from their name/action
- Natural emoji use — but no spam. Some characters may use none.
- Do NOT mention the need (hunger/social/etc.) directly
- Sound like a real person, not AI
- Varied styles: some melancholic, some cheerful, some mundane

Input:
{json.dumps(items, ensure_ascii=False, indent=2)}

Return ONLY a JSON array. Each element:
{{
  "id": "<same event_id>",
  "post_text": "<post content only — no handle, no prefix>"
}}"""

    try:
        model = get_model(NARRATOR_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 512,
                "temperature": 0.85
            }
        )
        parsed = extract_json_from_llm(resp.text)
        if not isinstance(parsed, list):
            return []
    except Exception as e:
        print(f"[WorldNarrator] SNS 배치 생성 실패: {e}")
        return []

    id_to_handle = {c["event_id"]: c["sns_handle"] for c in candidates}
    posts: list[str] = []

    for item in parsed:
        event_id  = item.get("id", "")
        post_text = item.get("post_text", "").strip()
        handle    = id_to_handle.get(event_id)
        if handle and post_text:
            posts.append(f"{handle} 님이 새 게시글을 올렸습니다: '{post_text}'")

    return posts


def _parse_ts(ts: str | None) -> datetime:
    """ISO 8601 또는 YYYYMMDD_HHMM → naive datetime. 파싱 실패 시 datetime.min."""
    if not ts:
        return datetime.min
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M")
    except ValueError:
        return datetime.min


# ════════════════════════════════════════════════════════════
# 캐릭터 그래프 관리 (구 world_builder.py)
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


async def _get_known_chars() -> dict[str, str]:
    """전체 캐릭터 이름→id 캐시를 반환한다. 최초 호출 시 DB 조회."""
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
    """캐릭터 캐시를 무효화한다."""
    global _known_chars_cache
    _known_chars_cache = None


def _resolve_identity(name: str, known: dict[str, str]) -> str | None:
    """이름이 known dict에 있으면 char_id 반환, 없으면 None."""
    if name in known:
        return known[name]
    if len(name) >= 2:
        for k, v in known.items():
            if name in k or k in name:
                return v
    return None


def _kor_to_roman_id(name_kor: str) -> str:
    """한국어 이름에서 타임스탬프 기반 영문 char_id를 생성한다."""
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


async def _increment_appearance(char_id: str) -> None:
    """StaticProfile의 appearance_count를 1 증가시킨다."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            SET sp.appearance_count = coalesce(sp.appearance_count, 0) + 1,
                sp.last_seen        = $ts
        """, cid=char_id, ts=datetime.now().isoformat())


async def _link_to_event(char_id: str, event_id: str) -> None:
    """캐릭터와 이벤트를 INVOLVED_IN 관계로 연결한다. 중복 방지."""
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
    """승격 기준 충족 시 Transient → Named NPC로 승격한다."""
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
            RETURN sp AS props
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
        resp = await model.generate_content_async(prompt, generation_config={})
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
