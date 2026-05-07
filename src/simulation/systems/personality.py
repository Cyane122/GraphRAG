# ================================
# src/simulation/systems/personality.py
#
# NPC 성격 변화(미세 변화 / 중대 변화)를 담당합니다.
# 긴밀한 관계 지속 시 미세한 성격 drift,
# 중대 이벤트(importance >= 9) 발생 시 Personality 노드 전면 재작성.
#
# Functions
#   - check_personality_drift(npc_id, pc_id, relationship_delta, event_importance, current_game_time) -> None : 조건에 따라 미세/중대 성격 변화 적용
# ================================

import json
from datetime import datetime

from src.config import MODEL_COMPLEX_UPDATER as DRIFT_MODEL
from src.core.database.driver import async_driver
from src.core.llm.client import get_model, extract_json_from_llm

_MICRO_DRIFT_AFFINITY_THRESHOLD = 65
_MICRO_DRIFT_COOLDOWN_DAYS      = 30
_MACRO_DRIFT_IMPORTANCE         = 9


async def check_personality_drift(
    npc_id:             str,
    pc_id:              str,
    relationship_delta: int,
    event_importance:   int,
    current_game_time:  datetime,
) -> None:
    """
    조건에 따라 micro-drift 또는 macro-drift를 실행한다.
    - macro: importance >= 9 이벤트 발생 시 성격 전면 재작성
    - micro: affinity >= 65이고 delta > 0일 때 30일 쿨다운 후 미세 조정
    """
    # macro drift가 micro보다 우선 — 중대 사건이면 미세 조정 불필요
    if event_importance >= _MACRO_DRIFT_IMPORTANCE:
        await _apply_macro_drift(npc_id, current_game_time)
        return

    if relationship_delta <= 0:
        return

    affinity = await _get_affinity(npc_id, pc_id)
    if affinity < _MICRO_DRIFT_AFFINITY_THRESHOLD:
        return

    props = await _load_personality_props(npc_id)
    if not props:
        return

    # 쿨다운: 마지막 drift 이후 충분한 시간이 지나야 다시 변화
    last_drifted = props.get("last_drifted_at")
    if last_drifted:
        try:
            last_dt = datetime.fromisoformat(last_drifted).replace(tzinfo=None)
            if (current_game_time - last_dt).days < _MICRO_DRIFT_COOLDOWN_DAYS:
                return
        except (ValueError, TypeError):
            pass

    await _apply_micro_drift(npc_id, pc_id, props, affinity, current_game_time)


async def _get_affinity(npc_id: str, pc_id: str) -> int:
    """RELATIONSHIP.affinity 반환. 관계가 없으면 0."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN r.affinity AS affinity
        """, a=npc_id, b=pc_id)
        row = await rec.single()
    return int(row["affinity"] or 0) if row else 0


async def _load_personality_props(npc_id: str) -> dict | None:
    """Personality.props JSON 로드. 없으면 None."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PERSONALITY]->(p:Personality)
            RETURN p.props AS props
        """, cid=npc_id)
        row = await rec.single()
    if not row or not row["props"]:
        return None

    raw = row["props"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


async def _save_personality_props(npc_id: str, props: dict) -> None:
    """Personality.props JSON 저장."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PERSONALITY]->(p:Personality)
            SET p.props = $props
        """, cid=npc_id, props=json.dumps(props, ensure_ascii=False))


async def _load_pc_personality_hint(pc_id: str) -> str:
    """PC StaticProfile에서 성격 관련 힌트를 추출한다. 최대 300자."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props
        """, cid=pc_id)
        row = await rec.single()
    if not row or not row["props"]:
        return ""

    raw = row["props"]
    data = {}
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
    elif isinstance(raw, dict):
        data = raw

    parts = [
        str(data[k])
        for k in ("personality", "core_traits", "character", "description")
        if data.get(k)
    ]
    return " / ".join(parts)[:300]


async def _load_high_importance_events(npc_id: str) -> str:
    """중요도 7 이상 이벤트 요약 최대 3개를 텍스트로 반환한다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event)
            WHERE e.importance >= 7
            RETURN e.summary AS summary, e.importance AS importance
            ORDER BY e.importance DESC, e.timestamp DESC
            LIMIT 3
        """, cid=npc_id)
        rows = await rec.data()
    return "\n".join(
        f"- [importance {r['importance']}] {r['summary']}"
        for r in rows if r.get("summary")
    ) or "(없음)"


async def _apply_micro_drift(
    npc_id:    str,
    pc_id:     str,
    props:     dict,
    affinity:  int,
    game_time: datetime,
) -> None:
    """
    깊은 관계(affinity >= 65)에서 NPC 성격을 아주 미세하게 조정한다.
    변화는 의도적으로 희미하게 — 독자가 눈치채지 못할 정도로.
    """
    pc_hint = await _load_pc_personality_hint(pc_id)

    prompt = f"""Character {npc_id} has spent a long time in a deep, close relationship with {pc_id} (affinity: {affinity}/100).

Current personality:
{json.dumps(props, ensure_ascii=False, indent=2)}

{f"Known tendencies of {pc_id}: {pc_hint}" if pc_hint else ""}

Apply MICRO personality drift: extremely subtle change, 90%+ unchanged.
- Adjust 1–2 traits very slightly in wording or emphasis
- Do NOT overhaul the character — they must still feel like themselves
- The change should be nearly imperceptible to a reader comparing before/after

Return the COMPLETE updated personality JSON (preserve ALL existing keys exactly, modify only 1–2 values slightly).
Also add: "last_drifted_at": "{game_time.isoformat()}"

Return ONLY valid JSON."""

    try:
        model = get_model(
            DRIFT_MODEL,
            system_prompt=f"You are subtly evolving {npc_id}'s personality due to long-term relationship influence. Changes must be minimal.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature":       0.4,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json",
            },
        )
        new_props = extract_json_from_llm(resp.text, source=f"micro_drift:{npc_id}")
        if not isinstance(new_props, dict):
            return

        new_props["last_drifted_at"] = game_time.isoformat()
        await _save_personality_props(npc_id, new_props)
        print(f"[Personality] micro-drift: {npc_id} (affinity={affinity})")

    except Exception as e:
        print(f"[Personality] micro-drift 실패 ({npc_id}): {e}")


async def _apply_macro_drift(npc_id: str, game_time: datetime) -> None:
    """
    중대 이벤트(importance >= 9) 후 NPC 성격을 전면 재작성한다.
    삶의 방향이 바뀔 정도의 충격이므로 가치관까지 재정립된다.
    """
    props = await _load_personality_props(npc_id)
    if not props:
        return

    event_context = await _load_high_importance_events(npc_id)
    drift_count   = int(props.get("macro_drift_count", 0)) + 1

    prompt = f"""Character {npc_id} has just experienced a life-altering event (importance 9–10).

Previous personality:
{json.dumps(props, ensure_ascii=False, indent=2)}

Major events they have experienced:
{event_context}

Rewrite their personality to reflect the transformation. This is a MACRO shift:
- Clear evolution in values, worldview, or emotional baseline
- Keep their core identity recognizable (same person, changed outlook)
- The new sample_line should reflect their new state of mind

Return the COMPLETE rewritten personality JSON (preserve all original keys, update their content).
Also set: "last_drifted_at": "{game_time.isoformat()}", "macro_drift_count": {drift_count}

Return ONLY valid JSON."""

    try:
        model = get_model(
            DRIFT_MODEL,
            system_prompt=f"You are rewriting {npc_id}'s personality after a life-altering event. The character must feel fundamentally changed yet still recognizable.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature":       0.6,
                "max_output_tokens": 1536,
                "response_mime_type": "application/json",
            },
        )
        new_props = extract_json_from_llm(resp.text, source=f"macro_drift:{npc_id}")
        if not isinstance(new_props, dict):
            return

        new_props["last_drifted_at"]   = game_time.isoformat()
        new_props["macro_drift_count"] = drift_count
        await _save_personality_props(npc_id, new_props)
        print(f"[Personality] ★ macro-drift #{drift_count}: {npc_id}")

    except Exception as e:
        print(f"[Personality] macro-drift 실패 ({npc_id}): {e}")
