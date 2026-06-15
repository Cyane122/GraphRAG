# ================================
# src/simulation/systems/memory/__init__.py
#
# 캐릭터별 기억(Memory) 노드 생성 및 시간 기반 풍화/왜곡/삭제를 담당합니다.
# 왜곡과 압축은 배치 처리로 LLM 호출 횟수를 최소화합니다.
#
# Functions
#   - _infer_signals_from_summary(summary: str) -> list[str] : 이벤트 요약에서 Korean keyword 기반 strong signal 태그 추론
#   - ensure_memories_for_event(event_id: str, summary: str, importance: int, char_ids: list[str], timestamp: str, embedding: list[float] | None = None, memory_type: str = "episodic", actor_response: str = "", signals: list[str] | None = None, source_type: str = "direct_experience", source_commit_id: str = "") -> None : Event에 관련된 캐릭터별 Memory 노드 생성 (게이트 통과 시)
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
from src.simulation.systems.memory.gate import GateDecision, apply_gate

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


def _fallback_memory_summary(char_id: str, summary: str, multi_character: bool) -> str:
    """캐릭터별 Memory 생성 실패 시 사용할 최소 주관화 문장을 반환한다."""
    if not multi_character:
        return summary
    return f"{char_id}의 기억: {summary}"


_SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "promise":         ["약속", "다짐", "맹세"],
    "appointment":     ["약속", "만나기로", "볼게"],
    "secret":          ["비밀", "숨기", "들키", "말하지 마"],
    "first_time":      ["처음", "첫 ", "최초", "처음으로"],
    "misunderstanding":["오해", "착각", "잘못 알"],
    "conflict":        ["갈등", "다툼", "싸움", "충돌", "언쟁", "화가 났"],
    "reconciliation":  ["화해", "용서", "사과"],
    "betrayal":        ["배신", "배반", "거짓말"],
    "gift":            ["선물", "줬다", "받았다"],
    "debt":            ["빌려", "갚", "빚"],
    "identity":        ["정체", "사실은", "알고 보니"],
    "boundary":        ["경계", "선을 넘"],
    "emotional_wound": ["상처", "트라우마", "아팠"],
    "favor":           ["부탁", "도움", "고마워"],
}


def _infer_signals_from_summary(summary: str) -> list[str]:
    """이벤트 요약 텍스트에서 강한 시그널 태그를 추론한다 (Korean keyword matching)."""
    if not summary:
        return []
    signals = []
    for signal, keywords in _SIGNAL_KEYWORDS.items():
        if any(kw in summary for kw in keywords):
            signals.append(signal)
    # deduplicate while preserving order
    seen: set[str] = set()
    return [s for s in signals if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]


def _confidence_from_type(memory_type: str, source_type: str) -> float:
    """memory_type + source_type 규칙으로 confidence를 산출한다 (LLM 불필요)."""
    base = {
        "direct_experience": 0.9,
        "hearsay": 0.6,
        "inference": 0.7,
        "gossip": 0.4,
    }.get(source_type, 0.75)
    # 오해 타입은 불확실성이 내재돼 있어 confidence를 낮춘다
    if memory_type == "misunderstanding":
        return min(base, 0.65)
    return base


async def _build_subjective_memory_summaries(
    event_id: str,
    summary: str,
    char_ids: list[str],
    actor_response: str,
) -> dict[str, str]:
    """Actor 응답과 객관 Event 요약에서 캐릭터별 주관 Memory 문장을 생성한다."""
    if not actor_response.strip() or not summary.strip() or not char_ids:
        return {}

    prompt = f"""Create character-specific Memory summaries for one roleplay event.

Rules:
- Korean, one concise sentence per character.
- Keep objective facts aligned with the Event summary.
- Write from what each character plausibly perceived or was involved in.
- Do not invent hidden motives, private thoughts, or facts not visible in the scene.
- If the scene gives no character-specific angle, still phrase the memory from that character's involvement.

Event id: {event_id}
Characters: {json.dumps(char_ids, ensure_ascii=False)}
Objective Event summary: {summary}

Scene:
{actor_response[:2500]}

Return ONLY JSON object:
{{"character_id": "subjective memory sentence"}}"""

    try:
        model = get_model(
            DECAY_MODEL,
            system_prompt="Create concise character-perspective memories from accepted roleplay scenes.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 768,
                "response_mime_type": "application/json",
            },
        )
        parsed = extract_json_from_llm(resp.text, source="memory_subjective_create")
    except Exception as exc:
        print(f"[DecayManager] 주관 Memory 생성 실패: {exc}")
        return {}

    if not isinstance(parsed, dict):
        return {}

    summaries: dict[str, str] = {}
    allowed = set(char_ids)
    for key, value in parsed.items():
        char_id = str(key).strip()
        memory = str(value or "").strip()
        if char_id in allowed and memory:
            summaries[char_id] = memory
    return summaries


async def _memory_embedding(memory_summary: str, shared_embedding: list[float] | None, event_summary: str) -> list[float] | None:
    """Memory 문장에 맞는 임베딩을 반환하되 동일 문장은 Event 임베딩을 재사용한다."""
    if shared_embedding is not None and memory_summary == event_summary:
        return shared_embedding
    if not memory_summary:
        return shared_embedding
    try:
        return await embed_async(memory_summary)
    except Exception as e:
        print(f"[DecayManager] Memory 임베딩 생성 실패: {e}")
    return shared_embedding


async def _reinforce_memory(
    session,
    mem_id: str,
    importance: int,
    timestamp: str,
) -> None:
    """기존 Memory의 salience/reinforced_count를 보수적으로 올린다. 새 노드를 만들지 않는다."""
    await session.run("""
        MATCH (m:Memory {id: $mid})
        SET m.salience           = coalesce(m.salience, 0.0) + 0.1,
            m.reinforced_count   = coalesce(m.reinforced_count, 0) + 1,
            m.last_reinforced_at = $ts,
            m.importance         = CASE
                WHEN $importance > coalesce(m.importance, 0) THEN $importance
                ELSE coalesce(m.importance, 0)
            END
    """, mid=mem_id, importance=importance, ts=timestamp)
    print(f"[MemoryGate] REINFORCE: {mem_id}")


# ════════════════════════════════════════════════════════════
# 1. Memory 노드 생성
# ════════════════════════════════════════════════════════════

async def ensure_memories_for_event(
    event_id:         str,
    summary:          str,
    importance:       int,
    char_ids:         list[str],
    timestamp:        str,
    embedding:        list[float] | None = None,
    memory_type:      str = "episodic",
    actor_response:   str = "",
    signals:          list[str] | None = None,
    source_type:      str = "direct_experience",
    source_commit_id: str = "",
) -> None:
    """
    Event에 관련된 캐릭터별 Memory 노드를 생성.
    객관 Event summary를 복제하지 않고 캐릭터별 주관 문장으로 저장한다.
    Memory Gate를 통과한 캐릭터만 생성하거나 reinforce한다.

    timestamp: 반드시 ISO 8601 형식 ("2024-03-08T08:00:00").
    """
    signals_list = signals or []
    # importance 3-4 + episodic 이벤트가 불필요하게 REJECT 되지 않도록
    # 명시적 signals가 없을 때 summary에서 keyword 기반으로 추론한다.
    if not signals_list and summary:
        signals_list = _infer_signals_from_summary(summary)
    signals_json = json.dumps(signals_list, ensure_ascii=False)
    confidence = _confidence_from_type(memory_type, source_type)

    emb = embedding
    if emb is None and summary:
        try:
            emb = await embed_async(summary)
        except Exception as e:
            print(f"[DecayManager] 임베딩 생성 실패: {e}")

    subjective_summaries = await _build_subjective_memory_summaries(
        event_id=event_id,
        summary=summary,
        char_ids=char_ids,
        actor_response=actor_response,
    )
    multi_character = len(char_ids) > 1

    # Phase 1: 캐릭터별 게이트 결정 (각자 세션을 열어 dedup 체크)
    # decisions[char_id] = (GateDecision, target_mem_id_to_reinforce)
    decisions: dict[str, tuple[GateDecision, str]] = {}
    for char_id in char_ids:
        decisions[char_id] = await apply_gate(
            char_id=char_id,
            event_id=event_id,
            importance=importance,
            signals=signals_list,
            memory_type=memory_type,
            source_commit_id=source_commit_id,
        )

    # Phase 2: 결정에 따라 CREATE / REINFORCE / skip
    async with async_driver.session() as session:
        for char_id in char_ids:
            mem_id = f"mem_{char_id}_{event_id}"
            decision, target_mem_id = decisions[char_id]

            if decision == GateDecision.REJECT:
                print(f"[MemoryGate] REJECT: {event_id} → {char_id} (importance={importance})")
                continue

            if decision == GateDecision.REINFORCE:
                # target_mem_id is the actual existing memory to reinforce.
                # It may differ from mem_id when the dedup matched via source_commit_id.
                await _reinforce_memory(session, target_mem_id, importance, timestamp)
                continue

            # CREATE path
            memory_summary = subjective_summaries.get(
                char_id,
                _fallback_memory_summary(char_id, summary, multi_character),
            )
            memory_emb = await _memory_embedding(memory_summary, emb, summary)

            await session.run("""
                CREATE (m:Memory {
                    id:                 $mid,
                    event_id:           $event_id,
                    char_id:            $char_id,
                    summary:            $summary,
                    embedding:          $emb,
                    memory_type:        $memory_type,
                    narrative_summary:  '',
                    state_summary:      $event_summary,
                    importance:         $importance,
                    distortion_level:   0.0,
                    summary_level:      0,
                    created_at:         $ts,
                    last_decayed_at:    $ts,
                    status:             'active',
                    source_commit_id:   $source_commit_id,
                    source_type:        $source_type,
                    confidence:         $confidence,
                    signals:            $signals_json,
                    salience:           0.0,
                    recall_count:       0,
                    last_recalled_at:   '',
                    reinforced_count:   0,
                    last_reinforced_at: '',
                    resolved_at:        ''
                })
            """,
                mid=mem_id, event_id=event_id, char_id=char_id,
                summary=memory_summary, emb=memory_emb, memory_type=memory_type,
                event_summary=summary, importance=importance, ts=timestamp,
                source_commit_id=source_commit_id, source_type=source_type,
                confidence=confidence, signals_json=signals_json,
            )

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
