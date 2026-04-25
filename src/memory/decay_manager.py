"""
캐릭터별 기억(Memory) 노드 관리.

역할:
  1. ensure_memories_for_event() — Event 생성 시 관련 캐릭터별 Memory 노드 생성
  2. run_decay()                 — 게임 내 시간 경과에 따라 기억 풍화 · 왜곡 · 삭제

Memory 노드:
  - Event 노드의 캐릭터별 주관적 복사본
  - 시간이 지날수록 성격 기반으로 미묘하게 왜곡됨
  - summary_level 0→1→2→3으로 압축되다 삭제

풍화 기준표 (게임 내 일 수):
  importance 0~2  : 생성 안 함 (자율행동 일상)
  importance 3~5  : 왜곡 30일 / 압축 60일 / 흔적 120일 / 삭제 240일
  importance 6~8  : 왜곡 90일 / 압축 180일 / 삭제 없음
  importance 9~10 : 영구 보존
"""

import os
from datetime import datetime

from src.utils.db_utils import async_driver
from src.utils.llm_utils import llm_client
from src.utils.embedder import embed_async

DECAY_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

_DECAY_TABLE = {
    (3, 5):  {"distort": 30,  "level1": 60,  "level2": 120, "delete": 240},
    (6, 8):  {"distort": 90,  "level1": 180, "level2": None, "delete": None},
    (9, 10): {"distort": None, "level1": None, "level2": None, "delete": None},
}

def _get_decay_rule(importance: int) -> dict:
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
    timestamp:  str,            # ISO 8601 형식 (isoformat())
    embedding:  list[float] | None = None,
) -> None:
    """
    Event에 관련된 캐릭터별 Memory 노드를 생성.
    importance 0~2 이벤트는 Memory 생성 생략 (자율행동 일상 → 프롬프트 노이즈).

    timestamp: 반드시 ISO 8601 형식 ("2024-03-08T08:00:00").
               YYYYMMDD_HHMM 포맷은 파싱 불가 → 호출부에서 .isoformat() 전달 필수.
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
        # Bug fix 1: _parse_dt는 naive datetime 반환 → current_game_time(naive)과 연산 가능
        created    = _parse_dt(m["created_at"])
        days_since = (current_game_time - created).days

        # 삭제
        if rule["delete"] and days_since >= rule["delete"] and m["level"] >= 2:
            await _delete_memory(m["mid"])
            continue

        # 흔적(level 2)
        if rule["level2"] and days_since >= rule["level2"] and m["level"] < 2:
            # Bug fix 6: traits fetch했지만 _compress_memory에서 사용 안 함 → 제거
            new_summary = await _compress_memory(m["summary"], level=2)
            await _update_memory(
                m["mid"], new_summary, old_embedding=None,
                summary_level=2, distortion=float(m["distortion"] or 0),
                game_time=current_game_time,
            )
            continue

        # 압축(level 1)
        if rule["level1"] and days_since >= rule["level1"] and m["level"] < 1:
            new_summary = await _compress_memory(m["summary"], level=1)
            await _update_memory(
                m["mid"], new_summary, old_embedding=None,
                summary_level=1, distortion=float(m["distortion"] or 0),
                game_time=current_game_time,
            )
            continue

        # 왜곡 (level 0 유지, summary만 슬쩍 변형)
        # Bug fix 7: distortion이 None일 수 있으므로 float(... or 0) 처리
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

    # Bug fix 4: trait_optimism → 실제 존재하는 trait 키로 교체
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
        resp = llm_client.messages.create(
            model=DECAY_MODEL,
            max_tokens=128,
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"[DecayManager] 왜곡 실패: {e}")
        return summary


async def _compress_memory(summary: str, level: int) -> str:
    """기억을 level에 맞게 압축."""
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
        resp = llm_client.messages.create(
            model=DECAY_MODEL,
            max_tokens=64,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
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
    """
    Memory 노드 summary 갱신 + 재임베딩.
    Bug fix 5: 임베딩 실패 시 old_embedding 유지 (None 저장 금지).
    old_embedding이 None이면 DB에서 기존 값 읽어서 유지.
    """
    new_emb = None
    try:
        new_emb = await embed_async(new_summary)
    except Exception as e:
        print(f"[DecayManager] 재임베딩 실패, 기존 임베딩 유지: {e}")

    async with async_driver.session() as session:
        if new_emb is not None:
            # 재임베딩 성공 → summary + embedding 모두 갱신
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
            # 재임베딩 실패 → embedding 필드 건드리지 않음
            await session.run("""
                MATCH (m:Memory {id: $mid})
                SET m.summary          = $summary,
                    m.summary_level    = $level,
                    m.distortion_level = $distortion,
                    m.last_decayed_at  = $ts
            """, mid=mem_id, summary=new_summary,
                 level=summary_level, distortion=distortion, ts=game_time.isoformat())


async def _delete_memory(mem_id: str) -> None:
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
    """
    ISO 8601 또는 YYYYMMDD_HHMM 형식 파싱 → naive datetime 반환.
    Bug fix 1: timezone-aware datetime 반환 금지.
               run_decay의 current_game_time이 naive이므로 타입 일치 필요.
    """
    if not dt_str:
        return datetime(2024, 1, 1)
    # ISO 8601 시도
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.replace(tzinfo=None)   # aware → naive 통일
    except ValueError:
        pass
    # YYYYMMDD_HHMM fallback
    try:
        return datetime.strptime(dt_str, "%Y%m%d_%H%M")
    except ValueError:
        pass
    return datetime(2024, 1, 1)