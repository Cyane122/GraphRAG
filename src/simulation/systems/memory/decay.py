# ================================
# src/simulation/systems/memory/decay.py
#
# 기억 풍화(decay) 루프 및 배치 LLM 압축 처리.
#
# Classes
#   - DecayReport : Observable result of one run_decay pass (per-bucket counts + llm_failed).
#
# Functions
#   - run_decay(current_game_time: datetime) -> DecayReport : 게임 내 시간 경과에 따라 기억 풍화·왜곡·삭제
#   - _compress_memories_batch(memories: list[dict], level: int) -> dict[str, str] | None : 여러 Memory를 한 번에 압축; None on hard LLM failure
#   - _get_decay_rule(importance: int) -> dict : importance에 해당하는 풍화 규칙 반환
#   - _parse_dt(dt_str: str | None) -> datetime : ISO 8601 문자열 파싱
# ================================

from dataclasses import dataclass
from datetime import datetime

from src.config import MODEL_STATE_UPDATER as DECAY_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
from src.simulation.systems.memory.distortion import (
    _delete_memory,
    _distort_memories_batch,
    _fetch_char_traits,
    _update_memory,
)


@dataclass(frozen=True)
class DecayReport:
    """run_decay 한 번의 관찰 가능한 결과.

    deleted/compressed_l1/compressed_l2/distorted: 버킷별 실제 변경 수.
    llm_failed: 압축 또는 왜곡 배치 중 하나라도 경성 실패(예외)했는가 — 이전에는 조용히 묻혔다.
    """
    deleted: int = 0
    compressed_l1: int = 0
    compressed_l2: int = 0
    distorted: int = 0
    llm_failed: bool = False

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


async def _compress_memories_batch(memories: list[dict], level: int) -> dict[str, str] | None:
    """
    여러 Memory를 한 번에 압축한다.
    Returns:
        {mid: new_summary} — 정상(빈 배열이면 빈 dict).
        None — 경성 실패(예외/파싱 실패/형식 위반) 시.
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
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 64 * len(memories) + 128,
                "temperature": 0.3,
                "response_mime_type": "application/json",
            },
        )
        # strict=True: 파싱 실패 시 {} 대신 LLMJsonError → 아래 except에서 None으로 신호한다.
        results = extract_json_from_llm(resp.text, source="memory_compress_batch", strict=True)
        if isinstance(results, list):
            return {
                item["id"]: item["summary"]
                for item in results
                if isinstance(item, dict) and "id" in item and "summary" in item
            }
        # 배치는 비어있지 않은데 결과가 배열이 아님 → 형식 위반(경성 실패로 본다).
        return None
    except Exception as e:
        print(f"[DecayManager] 배치 압축 실패 (level={level}): {e}")
        return None


async def run_decay(current_game_time: datetime) -> DecayReport:
    """
    days_passed > 0 일 때 호출.
    풍화 대상 기억을 삭제/압축/왜곡 버킷으로 분류한 뒤
    압축·왜곡은 배치 LLM 호출로 처리해 호출 횟수를 최소화한다.

    Returns: DecayReport — 버킷별 변경 수와 배치 LLM 경성 실패 여부(llm_failed)를 신호로 돌려준다.
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

    to_delete: list[str] = []
    to_compress_l2: list[dict] = []
    to_compress_l1: list[dict] = []
    to_distort_by_char: dict[str, list[dict]] = {}

    for m in memories:
        importance = int(m["importance"] or 3)
        rule = _get_decay_rule(importance)
        created = _parse_dt(m["created_at"])
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

    llm_failed = False
    deleted = compressed_l1 = compressed_l2 = distorted = 0

    for mid in to_delete:
        await _delete_memory(mid)
        deleted += 1

    if to_compress_l2:
        results = await _compress_memories_batch(to_compress_l2, level=2)
        if results is None:
            llm_failed = True
        else:
            for m in to_compress_l2:
                new_summary = results.get(m["mid"])
                if new_summary:
                    await _update_memory(m["mid"], new_summary, None, 2, float(m["distortion"] or 0), current_game_time)
                    compressed_l2 += 1

    if to_compress_l1:
        results = await _compress_memories_batch(to_compress_l1, level=1)
        if results is None:
            llm_failed = True
        else:
            for m in to_compress_l1:
                new_summary = results.get(m["mid"])
                if new_summary:
                    await _update_memory(m["mid"], new_summary, None, 1, float(m["distortion"] or 0), current_game_time)
                    compressed_l1 += 1

    for char_id, char_memories in to_distort_by_char.items():
        traits = await _fetch_char_traits(char_id)
        results = await _distort_memories_batch(char_memories, char_id, traits)
        if results is None:
            llm_failed = True
            continue
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
                await _update_memory(mid, new_summary, None, int(m["level"]), min(1.0, distortion + 0.25), current_game_time)
                distorted += 1

    report = DecayReport(
        deleted=deleted,
        compressed_l1=compressed_l1,
        compressed_l2=compressed_l2,
        distorted=distorted,
        llm_failed=llm_failed,
    )
    if any((deleted, compressed_l1, compressed_l2, distorted, llm_failed)):
        print(
            f"[DecayManager] decay 결과: delete={deleted} "
            f"compress(L1={compressed_l1},L2={compressed_l2}) distort={distorted} "
            f"llm_failed={llm_failed}"
        )
    return report
