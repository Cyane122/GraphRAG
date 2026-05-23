# ================================
# src/simulation/systems/memory/__init__.py
#
# 캐릭터별 기억(Memory) 노드 생성 및 시간 기반 풍화/왜곡/삭제를 담당합니다.
# 왜곡과 압축은 배치 처리로 LLM 호출 횟수를 최소화합니다.
#
# Functions
#   - ensure_memories_for_event(event_id: str, summary: str, importance: int, char_ids: list[str], timestamp: str, embedding: list[float] | None = None, memory_type: str = "episodic", narrative_summary: str = "", state_summary: str = "") -> None : Event에 관련된 캐릭터별 Memory 노드 생성
#   - run_decay(current_game_time: datetime) -> None : 게임 내 시간 경과에 따라 기억 풍화·왜곡·삭제
#   - distort_on_affinity_change(char_id: str, pc_id: str, affinity_delta: int, current_game_time: datetime) -> None : 호감도 급변 시 관련 기억을 즉시 재해석
# ================================

import json
from datetime import datetime

from src.config import MODEL_STATE_UPDATER as DECAY_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
from src.core.embedding.encoder import embed_async
from src.simulation.systems.memory.distortion import (
    _delete_memory,
    _distort_memories_batch,
    _fetch_char_traits,
    _update_memory,
    distort_on_affinity_change,
)

_DECAY_TABLE = {
    (0, 2):  {"distort": 14,  "level1": 30,  "level2": 60,  "delete": 120},
    (3, 5):  {"distort": 30,  "level1": 60,  "level2": 120, "delete": 240},
    (6, 8):  {"distort": 90,  "level1": 180, "level2": None, "delete": None},
    (9, 10): {"distort": None, "level1": None, "level2": None, "delete": None},
}

def _get_decay_rule(importance: int) -> dict:
    """importance 값에 해당하는 풍화 규칙 반환."""
    for (lo, hi), rule in _DECAY_TABLE.items():
        if lo <= importance <= hi:
            return rule
    return {"distort": None, "level1": None, "level2": None, "delete": None}


# ════════════════════════════════════════════════════════════
# 1. Memory 노드 생성
# ════════════════════════════════════════════════════════════

async def ensure_memories_for_event(
    event_id:   str,
    summary:    str,
    importance: int,
    char_ids:   list[str],
    timestamp:  str,
    embedding:  list[float] | None = None,
    memory_type: str = "episodic",
    narrative_summary: str = "",
    state_summary: str = "",
) -> None:
    """
    Event에 관련된 캐릭터별 Memory 노드를 생성.
    중요도와 관계없이 Event에 관련된 각 캐릭터의 원본 Memory를 생성한다.

    timestamp: 반드시 ISO 8601 형식 ("2024-03-08T08:00:00").
    """
    emb = embedding
    if emb is None and summary:
        try:
            emb = await embed_async(summary)
        except Exception as e:
            print(f"[DecayManager] 임베딩 생성 실패: {e}")

    async with async_driver.session() as session:
        for char_id in char_ids:
            mem_id = f"mem_{char_id}_{event_id}"

            rec = await session.run(
                "MATCH (m:Memory {id: $mid}) RETURN m.id AS id",
                mid=mem_id,
            )
            if await rec.single():
                continue

            await session.run("""
                CREATE (m:Memory {
                    id:               $mid,
                    event_id:         $event_id,
                    char_id:          $char_id,
                    summary:          $summary,
                    embedding:        $emb,
                    memory_type:      $memory_type,
                    narrative_summary: $narrative_summary,
                    state_summary:    $state_summary,
                    importance:       $importance,
                    distortion_level: 0.0,
                    summary_level:    0,
                    created_at:       $ts,
                    last_decayed_at:  $ts
                })
            """, mid=mem_id, event_id=event_id, char_id=char_id,
                 summary=summary, emb=emb, memory_type=memory_type,
                 narrative_summary=narrative_summary, state_summary=state_summary,
                 importance=importance, ts=timestamp)

            await session.run("""
                MATCH (c:Character {id: $cid}), (m:Memory {id: $mid})
                CREATE (c)-[:REMEMBERS]->(m)
            """, cid=char_id, mid=mem_id)

            await session.run("""
                MATCH (m:Memory {id: $mid}), (e:Event {id: $eid})
                CREATE (m)-[:OF_EVENT]->(e)
            """, mid=mem_id, eid=event_id)

    print(f"[DecayManager] Memory 생성: {event_id} → {char_ids}")


# ════════════════════════════════════════════════════════════
# 2. 풍화 루프 (배치 처리)
# ════════════════════════════════════════════════════════════

async def run_decay(current_game_time: datetime) -> None:
    """
    days_passed > 0 일 때 호출.
    풍화 대상 기억을 삭제/압축/왜곡 버킷으로 분류한 뒤
    압축·왜곡은 배치 LLM 호출로 처리해 호출 횟수를 최소화한다.
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:REMEMBERS]->(m:Memory)
            RETURN m.id               AS mid,
                   m.char_id          AS char_id,
                   m.summary          AS summary,
                   m.importance       AS importance,
                   m.distortion_level AS distortion,
                   m.summary_level    AS level,
                   m.created_at       AS created_at
        """)
        memories = await rec.data()

    for m in memories:
        if isinstance(m.get("mid"), list):
            m["mid"] = m["mid"][0] if m["mid"] else ""
        if isinstance(m.get("char_id"), list):
            m["char_id"] = m["char_id"][0] if m["char_id"] else ""

    to_delete:                    list[str]         = []
    to_compress_l2:               list[dict]        = []
    to_compress_l1:               list[dict]        = []
    to_distort_by_char: dict[str, list[dict]]       = {}

    for m in memories:
        importance = int(m["importance"] or 3)
        rule       = _get_decay_rule(importance)
        created    = _parse_dt(m["created_at"])
        days_since = (current_game_time - created).days

        if rule["delete"] and days_since >= rule["delete"] and m["level"] >= 2:
            to_delete.append(m["mid"])
            continue

        if rule["level2"] and days_since >= rule["level2"] and m["level"] < 2:
            to_compress_l2.append(m)
            continue

        if rule["level1"] and days_since >= rule["level1"] and m["level"] < 1:
            to_compress_l1.append(m)
            continue

        distortion = float(m["distortion"] or 0)
        if rule["distort"] and days_since >= rule["distort"] and distortion < 0.5:
            char_id = m["char_id"]
            to_distort_by_char.setdefault(char_id, []).append(m)

    # ── 삭제 ─────────────────────────────────────────────────
    for mid in to_delete:
        await _delete_memory(mid)

    # ── Level 2 압축 (배치) ──────────────────────────────────
    if to_compress_l2:
        results = await _compress_memories_batch(to_compress_l2, level=2)
        for m in to_compress_l2:
            new_summary = results.get(m["mid"])
            if new_summary:
                await _update_memory(
                    m["mid"], new_summary, None,
                    2, float(m["distortion"] or 0), current_game_time,
                )

    # ── Level 1 압축 (배치) ──────────────────────────────────
    if to_compress_l1:
        results = await _compress_memories_batch(to_compress_l1, level=1)
        for m in to_compress_l1:
            new_summary = results.get(m["mid"])
            if new_summary:
                await _update_memory(
                    m["mid"], new_summary, None,
                    1, float(m["distortion"] or 0), current_game_time,
                )

    # ── 왜곡 (char_id별 배치) ───────────────────────────────
    for char_id, char_memories in to_distort_by_char.items():
        traits  = await _fetch_char_traits(char_id)
        results = await _distort_memories_batch(char_memories, char_id, traits)
        for m in char_memories:
            mid = m["mid"]
            if isinstance(mid, list):
                mid = mid[0] if mid else ""
            mid = str(mid)
            if mid not in results:
                continue
            new_summary = results[mid]
            if new_summary != m["summary"]:
                distortion = float(m["distortion"] or 0)
                await _update_memory(
                    mid, new_summary, None,
                    int(m["level"]), min(1.0, distortion + 0.25),
                    current_game_time,
                )


# ════════════════════════════════════════════════════════════
# 배치 LLM 호출
# ════════════════════════════════════════════════════════════

async def _compress_memories_batch(memories: list[dict], level: int) -> dict[str, str]:
    """
    여러 Memory를 한 번에 압축한다.
    Returns: {mid: new_summary} — 실패한 항목은 포함되지 않음.
    """
    instruction = (
        "Compress each memory to 1 short sentence. Keep the emotional core."
        if level == 1
        else "Reduce each memory to a single fragment: just a feeling or vague impression. Korean OK."
    )
    items = "\n".join(
        f'{i + 1}. [id:{m["mid"]}] {m["summary"]}'
        for i, m in enumerate(memories)
    )

    prompt = f"""{instruction}

Memories:
{items}

Return ONLY a JSON array:
[{{"id": "<mid>", "summary": "<compressed>"}}, ...]"""

    try:
        model = get_model(DECAY_MODEL, system_prompt="You are a memory compressor. Reduce information while keeping the emotional core.")
        resp  = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 64 * len(memories) + 128,
                "temperature": 0.3,
                "response_mime_type": "application/json",
            },
        )
        results = extract_json_from_llm(resp.text, source="memory_compress_batch")
        if isinstance(results, list):
            return {
                item["id"]: item["summary"]
                for item in results
                if isinstance(item, dict) and "id" in item and "summary" in item
            }
    except Exception as e:
        print(f"[DecayManager] 배치 압축 실패 (level={level}): {e}")
    return {}


def _parse_dt(dt_str: str | None) -> datetime:
    """ISO 8601 또는 YYYYMMDD_HHMM 문자열을 naive datetime으로 파싱한다."""
    if not dt_str:
        return datetime(2024, 1, 1)
    try:
        return datetime.fromisoformat(dt_str).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(dt_str, "%Y%m%d_%H%M")
    except ValueError:
        pass
    return datetime(2024, 1, 1)
