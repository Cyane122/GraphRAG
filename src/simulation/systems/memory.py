# ================================
# src/simulation/systems/memory.py
#
# 캐릭터별 기억(Memory) 노드 생성 및 시간 기반 풍화/왜곡/삭제를 담당합니다.
#
# Functions
#   - ensure_memories_for_event(event_id, summary, importance, char_ids, timestamp, embedding) -> None : Event에 관련된 캐릭터별 Memory 노드 생성
#   - run_decay(current_game_time: datetime) -> None : 게임 내 시간 경과에 따라 기억 풍화·왜곡·삭제
# ================================

import os
from datetime import datetime

from src.core.database.driver import async_driver
from src.core.llm.client import get_model
from src.core.embedding.encoder import embed_async

DECAY_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

_DECAY_TABLE = {
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
) -> None:
    """
    Event에 관련된 캐릭터별 Memory 노드를 생성.
    importance 0~2 이벤트는 Memory 생성 생략 (자율행동 일상 → 프롬프트 노이즈).

    timestamp: 반드시 ISO 8601 형식 ("2024-03-08T08:00:00").
    """
    if importance <= 2:
        return

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
                    importance:       $importance,
                    distortion_level: 0.0,
                    summary_level:    0,
                    created_at:       $ts,
                    last_decayed_at:  $ts
                })
            """, mid=mem_id, event_id=event_id, char_id=char_id,
                 summary=summary, emb=emb, importance=importance, ts=timestamp)

            await session.run("""
                MATCH (c:Character {id: $cid}), (m:Memory {id: $mid})
                CREATE (c)-[:REMEMBERS]->(m)
            """, cid=char_id, mid=mem_id)

    print(f"[DecayManager] Memory 생성: {event_id} → {char_ids}")


# ════════════════════════════════════════════════════════════
# 2. 풍화 루프
# ════════════════════════════════════════════════════════════

async def run_decay(current_game_time: datetime) -> None:
    """
    manager_agent에서 days_passed > 0 일 때 호출.
    current_game_time: naive datetime (GlobalState 기준).
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
        importance = int(m["importance"] or 3)
        rule       = _get_decay_rule(importance)
        created    = _parse_dt(m["created_at"])
        days_since = (current_game_time - created).days

        if rule["delete"] and days_since >= rule["delete"] and m["level"] >= 2:
            await _delete_memory(m["mid"])
            continue

        if rule["level2"] and days_since >= rule["level2"] and m["level"] < 2:
            new_summary = await _compress_memory(m["summary"], level=2)
            await _update_memory(
                m["mid"], new_summary, old_embedding=None,
                summary_level=2, distortion=float(m["distortion"] or 0),
                game_time=current_game_time,
            )
            continue

        if rule["level1"] and days_since >= rule["level1"] and m["level"] < 1:
            new_summary = await _compress_memory(m["summary"], level=1)
            await _update_memory(
                m["mid"], new_summary, old_embedding=None,
                summary_level=1, distortion=float(m["distortion"] or 0),
                game_time=current_game_time,
            )
            continue

        distortion = float(m["distortion"] or 0)
        if rule["distort"] and days_since >= rule["distort"] and distortion < 0.5:
            traits      = await _fetch_char_traits(m["char_id"])
            new_summary = await _distort_memory(m["summary"], traits, m["char_id"])
            if new_summary and new_summary != m["summary"]:
                await _update_memory(
                    m["mid"], new_summary, old_embedding=None,
                    summary_level=m["level"],
                    distortion=min(1.0, distortion + 0.25),
                    game_time=current_game_time,
                )


# ════════════════════════════════════════════════════════════
# 내부 헬퍼
# ════════════════════════════════════════════════════════════

async def _distort_memory(summary: str, traits: dict, char_id: str) -> str:
    """Haiku가 traits 기반으로 memory를 캐릭터 관점에서 살짝 왜곡."""
    trait_hints = []

    if traits.get("trait_self_esteem", 0) > 0.5:
        trait_hints.append("tends to remember things more positively, downplays negatives")
    if traits.get("trait_anxiety_prone", 0) > 0.5:
        trait_hints.append("tends to amplify threatening or upsetting elements")
    if traits.get("trait_self_esteem", 0) < -0.1:
        trait_hints.append("tends to underplay their own role or worth")
    if traits.get("trait_stubbornness", 0) > 0.5:
        trait_hints.append("tends to remember their own position as more justified")
    if traits.get("trait_attachment", 0) > 0.7:
        trait_hints.append("keeps partner-related details vivid, fades other details")
    if traits.get("trait_jealousy", 0) > 0.5:
        trait_hints.append("tends to over-remember moments of perceived rivalry or slight")

    if not trait_hints:
        return summary

    hint_str = "; ".join(trait_hints)
    system_instruction = f"You are {char_id}'s inner subconscious. Rewrite memories from their subjective perspective."

    prompt = f"""Rewrite this memory summary from {char_id}'s subjective perspective.
Apply subtle distortion based on their personality: {hint_str}

Rules:
- Keep the core facts intact. Only shift emphasis, tone, or minor details.
- Do NOT change who was present or what major event occurred.
- 1~2 sentences max. Korean OK.
- Return ONLY the rewritten summary. No explanation.

Original summary:
{summary}"""

    try:
        model = get_model(model_name=DECAY_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"max_output_tokens": 128, "temperature": 0.7}
        )
        return resp.text.strip()
    except Exception as e:
        print(f"[DecayManager] 왜곡 실패: {e}")
        return summary


async def _compress_memory(summary: str, level: int) -> str:
    """기억을 level에 맞게 압축."""
    system_instruction = "You are a memory compressor. Reduce information while keeping the emotional core."

    instruction = (
        "Compress to 1 short sentence. Keep the emotional core."
        if level == 1
        else "Reduce to a single fragment: just a feeling or vague impression. Korean OK."
    )
    prompt = f"""{instruction}

Original:
{summary}

Return ONLY the compressed version. No explanation."""

    try:
        model = get_model(DECAY_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"max_output_tokens": 64, "temperature": 0.3}
        )
        return resp.text.strip()
    except Exception as e:
        print(f"[DecayManager] 압축 실패: {e}")
        return summary


async def _update_memory(
    mem_id:        str,
    new_summary:   str,
    old_embedding: list[float] | None,
    summary_level: int,
    distortion:    float,
    game_time:     datetime,
) -> None:
    """Memory 노드 요약/임베딩/레벨/왜곡도를 갱신한다."""
    new_emb = None
    try:
        new_emb = await embed_async(new_summary)
    except Exception as e:
        print(f"[DecayManager] 재임베딩 실패, 기존 임베딩 유지: {e}")

    async with async_driver.session() as session:
        if new_emb is not None:
            await session.run("""
                MATCH (m:Memory {id: $mid})
                SET m.summary          = $summary,
                    m.embedding        = $emb,
                    m.summary_level    = $level,
                    m.distortion_level = $distortion,
                    m.last_decayed_at  = $ts
            """, mid=mem_id, summary=new_summary, emb=new_emb,
                 level=summary_level, distortion=distortion, ts=game_time.isoformat())
        else:
            await session.run("""
                MATCH (m:Memory {id: $mid})
                SET m.summary          = $summary,
                    m.summary_level    = $level,
                    m.distortion_level = $distortion,
                    m.last_decayed_at  = $ts
            """, mid=mem_id, summary=new_summary,
                 level=summary_level, distortion=distortion, ts=game_time.isoformat())


async def _delete_memory(mem_id: str) -> None:
    """Memory 노드를 그래프에서 완전 삭제한다."""
    async with async_driver.session() as session:
        await session.run(
            "MATCH (m:Memory {id: $mid}) DETACH DELETE m",
            mid=mem_id,
        )
    print(f"[DecayManager] Memory 삭제: {mem_id}")


async def _fetch_char_traits(char_id: str) -> dict:
    """DynamicState → StaticProfile 순으로 trait_* 필드 로드."""
    async with async_driver.session() as session:
        for rel in ("HAS_STATE", "HAS_PROFILE"):
            rec = await session.run(f"""
                MATCH (c:Character {{id: $cid}})-[:{rel}]->(n)
                RETURN properties(n) AS props
            """, cid=char_id)
            row = await rec.single()
            if row and row["props"]:
                props  = dict(row["props"])
                traits = {k: v for k, v in props.items() if k.startswith("trait_")}
                if traits:
                    return traits
    return {}


def _parse_dt(dt_str: str | None) -> datetime:
    """ISO 8601 또는 YYYYMMDD_HHMM 문자열을 naive datetime으로 파싱한다."""
    if not dt_str:
        return datetime(2024, 1, 1)
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.strptime(dt_str, "%Y%m%d_%H%M")
    except ValueError:
        pass
    return datetime(2024, 1, 1)
