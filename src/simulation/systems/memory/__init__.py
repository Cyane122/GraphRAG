# ================================
# src/simulation/systems/memory/__init__.py
#
# 캐릭터별 기억(Memory) 노드 생성 및 시간 기반 풍화/왜곡/삭제를 담당합니다.
# 왜곡·압축은 decay.py, 풍화 배치 처리는 배치 LLM 호출로 최소화합니다.
#
# Functions
#   - ensure_memories_for_event(event_id, summary, importance, char_ids, timestamp, ...) -> None : Event에 관련된 캐릭터별 Memory 노드 생성 (게이트 통과 시)
#   - run_decay(current_game_time: datetime) -> None : re-exported from decay.py
#   - distort_on_affinity_change(char_id, pc_id, affinity_delta, current_game_time) -> None : re-exported from distortion.py
#   - _infer_signals_from_summary(summary: str) -> list[str] : 이벤트 요약에서 Korean keyword 기반 strong signal 태그 추론
# ================================

import json
from datetime import datetime

from src.config import MODEL_STATE_UPDATER as DECAY_MODEL
from src.core.database import async_driver
from src.core.embedding.encoder import embed_async
from src.core.llm.client import extract_json_from_llm, get_model
from src.simulation.systems.memory.decay import run_decay
from src.simulation.systems.memory.distortion import distort_on_affinity_change
from src.simulation.systems.memory.gate import GateDecision, apply_gate

__all__ = [
    "ensure_memories_for_event",
    "run_decay",
    "distort_on_affinity_change",
    "_infer_signals_from_summary",
]

_SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "promise":          ["약속", "다짐", "맹세"],
    "appointment":      ["약속", "만나기로", "볼게"],
    "secret":           ["비밀", "숨기", "들키", "말하지 마"],
    "first_time":       ["처음", "첫 ", "최초", "처음으로"],
    "misunderstanding": ["오해", "착각", "잘못 알"],
    "conflict":         ["갈등", "다툼", "싸움", "충돌", "언쟁", "화가 났"],
    "reconciliation":   ["화해", "용서", "사과"],
    "betrayal":         ["배신", "배반", "거짓말"],
    "gift":             ["선물", "줬다", "받았다"],
    "debt":             ["빌려", "갚", "빚"],
    "identity":         ["정체", "사실은", "알고 보니"],
    "boundary":         ["경계", "선을 넘"],
    "emotional_wound":  ["상처", "트라우마", "아팠"],
    "favor":            ["부탁", "도움", "고마워"],
}


def _infer_signals_from_summary(summary: str) -> list[str]:
    """이벤트 요약 텍스트에서 강한 시그널 태그를 추론한다 (Korean keyword matching)."""
    if not summary:
        return []
    signals = []
    for signal, keywords in _SIGNAL_KEYWORDS.items():
        if any(kw in summary for kw in keywords):
            signals.append(signal)
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
    if memory_type == "misunderstanding":
        return min(base, 0.65)
    return base


def _fallback_memory_summary(char_id: str, summary: str, multi_character: bool) -> str:
    """캐릭터별 Memory 생성 실패 시 사용할 최소 주관화 문장을 반환한다."""
    if not multi_character:
        return summary
    return f"{char_id}의 기억: {summary}"


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


async def _memory_embedding(
    memory_summary: str,
    shared_embedding: list[float] | None,
    event_summary: str,
) -> list[float] | None:
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


async def _reinforce_memory(session, mem_id: str, importance: int, timestamp: str) -> None:
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
# Memory 노드 생성
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
                await _reinforce_memory(session, target_mem_id, importance, timestamp)
                continue

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
