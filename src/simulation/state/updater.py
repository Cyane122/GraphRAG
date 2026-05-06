# ================================
# src/simulation/state/updater.py
#
# 상태 업데이트 파이프라인 전체를 담당합니다.
# Classifier + Complex Updater + Relationship Status를 단일 LLM 호출로 처리합니다.
#
# Functions
#   - process_actor_response(actor_response, npc_id, pc_id, scene_types, scene_chars, world_config) -> str | None : Actor 응답 분석 후 상태 업데이트. 임신 OOC 메시지 반환.
#   - apply_time_updates(plan, base_time, pc_id, npc_id) -> datetime : 시간 계획을 DB에 반영
#   - delegate_complex_update(actor_response, npc_id, pc_id, initial_changes, event_only, world_config, scene_chars) -> str | None : event_only 경로 전용 복합 업데이트
#
# 관계 깊이 파이프라인 (process_actor_response 내부에서 호출):
#   - reputation.propagate_gossip : 중요 이벤트 후 주변 NPC에게 소문 전파
#   - memory.distort_on_affinity_change : 호감도 급변 시 공유 기억 즉시 재해석
#   - personality.check_personality_drift : micro / macro 성격 변화 체크
# ================================

import json
import re
from datetime import datetime, timedelta

from src.core.database import (
    async_driver,
    update_dynamic_state,
    update_relationship_affinity,
    move_location,
    advance_cycle_day,
    get_in_universe_time,
)
from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.embedding.encoder import embed_async
from src.simulation.systems.memory import ensure_memories_for_event
from src.simulation.systems.organic import process_ejaculation


# ════════════════════════════════════════════════════════════
# 분류 게이트
# ════════════════════════════════════════════════════════════

_CHANGE_PATTERN = re.compile(
    r"다쳤|부상|병원|골절|삐었|쓰러|기절|아프|열이|입원|"
    r"이동했|나갔|도착|들어왔|장소|"
    r"스트레스|화났|슬퍼|불안|우울|힘들|짜증|무너|"
    r"싸웠|화해|고백|사귀|헤어|"
    r"injured|hospitalized|arrived|moved|stressed"
)
_ALWAYS_CLASSIFY = {"intimate", "workplace", "physical"}


def _needs_classification(actor_response: str, scene_types: list[str]) -> bool:
    """LLM 호출이 필요한지 판단. False면 업데이트 전체 스킵."""
    if any(t in scene_types for t in _ALWAYS_CLASSIFY):
        return True
    return bool(_CHANGE_PATTERN.search(actor_response))


def _sanitize_stress_level(value) -> int | None:
    """
    stress_level / workplace_stress_level 필드를 정수로 정규화한다.
    LLM이 문자열로 반환한 경우 매핑, 범위 외 값은 None으로 거부.
    """
    if isinstance(value, int) and 0 <= value <= 10:
        return value
    if isinstance(value, str):
        try:
            n = int(value)
            if 0 <= n <= 10:
                return n
        except (ValueError, TypeError):
            mapping = {
                "none": 0, "very low": 1, "low": 2,
                "medium-low": 4, "medium": 5, "mid": 5,
                "medium-high": 6, "high": 8, "very high": 9, "max": 10,
            }
            return mapping.get(value.lower().strip())
    return None


# ════════════════════════════════════════════════════════════
# 통합 LLM 호출 (Classifier + Complex Updater + Relationship Status)
# ════════════════════════════════════════════════════════════

async def _run_combined_update(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    world_config:   dict | None = None,
) -> dict:
    """
    단일 LLM 호출로 다음을 처리한다:
      1. LITERAL/FIGURATIVE 분류 → DynamicState 변경 추출
      2. 관계 호감도 delta 계산
      3. Event 생성 판단
      4. importance ≥ 7 이벤트 시 relationship_status 재작성 (new_event 내 포함)

    Returns:
        {dynamic_state, relationship_delta, new_event,
         ts_acceptance_delta, northern_attachment_delta}
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    ts_scoring = bool(world_config and world_config.get("ts_scoring_enabled"))

    ts_section = ""
    ts_json_fields = ""
    if ts_scoring:
        ts_section = f"""
## TS/North Acceptance Scoring (ONLY for NPC {npc_id})

ts_acceptance_delta — involuntary biological/physical feminine submission this turn.
DEFAULT 0. Increment only for:
  +1: Subtle involuntary reaction (caught breath, hesitation, warmth suppressed)
  +2: Clear involuntary feminine response she cannot deny
  +3: Significant biological defeat (menstrual pain forcing yield, arousal until she flees)
  +5: Major submission, shattering of male self. Extremely rare.
NEVER negative. Max +5 per turn.

northern_attachment_delta — genuine warmth/solidarity toward North characters this turn.
DEFAULT 0. Increment only for:
  +1: Small warmth moment from a North character
  +2: Genuine solidarity or recognition moment
  +3: Significant emotional shift. Rare.
NEVER negative. Max +3 per turn.
"""
        ts_json_fields = '\n  "ts_acceptance_delta": 0,\n  "northern_attachment_delta": 0,'

    system_instruction = f"""You are a precise state manager for a Korean roleplay system.
Analyze the scene. Return updates ONLY for Main NPC ({npc_id}) and the ({npc_id})↔({pc_id}) relationship.
CRITICAL: Do NOT assign secondary characters' injuries or emotions to {npc_id}."""

    prompt = f"""## Classification
LITERAL: Direct physical events — injury, illness, confirmed physical state, clothing description
  "팔을 다쳤어" / "발목을 삐었다" / "코트를 걸쳤다" / "잠옷 바지를 입은 채"
FIGURATIVE: Emotional or metaphorical — NEVER touch physical_condition / injury_detail
  "심장이 터질 것 같아" / "죽고 싶다" / "온몸이 녹아내리는 것 같아"

## DynamicState — extract ONLY actually changed fields
Always extractable:
- mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
- mental_condition: stable/stressed/anxious/depressed/exhausted
- stress_level: 0–10 integer
  10=가족사망·부정발각, 5=시험실패·심각한다툼, 3=지갑분실·사소한언쟁, 0=완벽한하루
- workplace_stress_level: 0–10 integer
  10=해고·공개망신, 6=지속불쾌접촉, 2=까다로운손님, 0=순탄한근무
- outfit: current clothing IF explicitly described (Korean ≤25 chars). Omit entirely if not mentioned.
- injury_marks: "없음" or visible injury description. Only update if changed this scene.

LITERAL only:
- physical_condition: healthy/fatigued/injured/ill/hospitalized
- injury_detail: body part + type (LITERAL events only)

## Relationship delta
Integer (e.g. +5 or -10). null if unchanged.

## Event creation — only if importance ≥ 3
9–10: Life-altering (hospitalization after accident, first confession, surgery)
6–8: Significant (first meeting, major fight + real reconciliation, first intimacy, near-breakup)
3–5: Noteworthy (new injury at clinic for first time, new character met, argument with lasting tension)
0–2: DO NOT create (meal, routine chat, daily interaction, follow-up visit for existing injury)

ID format: {{location}}_{{description}}_{{YYYYMMDD_HHMM}}

## Relationship Status
ONLY when new_event.importance ≥ 7: add "new_relationship_status" to new_event.
1–3 sentences English. Describe the current relationship state AFTER this event. Be specific about what shifted.
{ts_section}
Return ONLY valid JSON:
{{
  "dynamic_state": {{}},{ts_json_fields}
  "relationship_delta": null,
  "new_event": null
}}

When creating an event replace new_event with:
{{
  "id": "string",
  "summary": "1–2 sentence Korean summary",
  "importance": 0,
  "impact": "brief description"
}}
When importance ≥ 7 also add:
  "new_relationship_status": "English 1–3 sentences"

Scene:
{actor_response[:2000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0, "max_output_tokens": 2048,
                           "response_mime_type": "application/json"},
    )

    plan = extract_json_from_llm(response.text, source="combined_updater")
    if not isinstance(plan, dict):
        plan = {}
    return plan


# ════════════════════════════════════════════════════════════
# 상태 업데이트 — 메인 경로
# ════════════════════════════════════════════════════════════

async def process_actor_response(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    scene_types:    list[str] | None = None,
    scene_chars:    list[str] | None = None,
    world_config:   dict | None = None,
) -> str | None:
    """
    Actor 응답을 단일 LLM 호출로 분석하여 상태·관계·이벤트를 일괄 업데이트한다.
    임신 관련 OOC 메시지가 발생하면 반환, 없으면 None.
    """
    from src.simulation.systems.social import resolve_and_update as wb_resolve

    if scene_types and not _needs_classification(actor_response, scene_types):
        print("[StateUpdater] 스킵 (변화 키워드 없음)")
        if world_config and scene_chars:
            await wb_resolve(scene_chars, npc_id, pc_id, world_config)
        return None

    plan = await _run_combined_update(actor_response, npc_id, pc_id, world_config)
    if not plan:
        return None

    # ── DynamicState 업데이트 ────────────────────────────────
    state = dict(plan.get("dynamic_state") or {})
    for field in ("stress_level", "workplace_stress_level"):
        if field in state:
            sanitized = _sanitize_stress_level(state[field])
            if sanitized is None:
                del state[field]
            else:
                state[field] = sanitized
    if state:
        await update_dynamic_state(npc_id, state)

    # ── 관계 호감도 ──────────────────────────────────────────
    delta = plan.get("relationship_delta")
    if delta and isinstance(delta, (int, float)):
        d = int(delta)
        await update_relationship_affinity(npc_id, pc_id, d)
        await update_relationship_affinity(pc_id, npc_id, d)

    # ── TS 점수 (세계별 옵션) ────────────────────────────────
    if world_config and world_config.get("ts_scoring_enabled"):
        await _update_acceptance_scores(
            npc_id,
            int(plan.get("ts_acceptance_delta") or 0),
            int(plan.get("northern_attachment_delta") or 0),
        )

    # ── 이벤트 생성 + Relationship Status 갱신 ───────────────
    new_event = plan.get("new_event")
    if new_event:
        await _create_event(new_event, npc_id, pc_id)
        new_status = new_event.get("new_relationship_status")
        if new_status and new_event.get("importance", 0) >= 7:
            await _apply_relationship_status(npc_id, pc_id, new_status)

    # ── 소셜 리졸버 ─────────────────────────────────────────
    if world_config and scene_chars:
        try:
            await wb_resolve(
                char_names       = scene_chars,
                main_npc_id      = npc_id,
                pc_id            = pc_id,
                world_config     = world_config,
                event_id         = new_event.get("id") if new_event else None,
                event_importance = new_event.get("importance", 0) if new_event else 0,
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")

    # ── 관계 깊이 파이프라인 ─────────────────────────────────
    # 소문 전파 / 호감도 급변 왜곡 / 성격 변화. 시간은 한 번만 조회.
    _d        = int(delta or 0)
    _imp      = new_event.get("importance", 0) if new_event else 0
    _depth_ts = await _get_current_iso_time()
    _depth_dt = datetime.fromisoformat(_depth_ts)

    # 소문 전파: 중요 이벤트 + 유의미한 호감도 변화가 있을 때
    if new_event and _imp >= 5 and abs(_d) >= 3:
        try:
            from src.simulation.systems.reputation import propagate_gossip
            await propagate_gossip(
                event_summary      = new_event.get("summary", ""),
                event_importance   = _imp,
                relationship_delta = _d,
                source_npc_id      = npc_id,
                pc_id              = pc_id,
                timestamp_iso      = _depth_ts,
            )
        except Exception as e:
            print(f"[Updater] 소문 전파 실패 (무시): {e}")

    # 호감도 급변 시 공유 기억 즉시 재해석
    if abs(_d) >= 10:
        try:
            from src.simulation.systems.memory import distort_on_affinity_change
            await distort_on_affinity_change(npc_id, pc_id, _d, _depth_dt)
        except Exception as e:
            print(f"[Updater] 기억 왜곡 실패 (무시): {e}")

    # 성격 변화 체크 (micro / macro)
    try:
        from src.simulation.systems.personality import check_personality_drift
        await check_personality_drift(
            npc_id             = npc_id,
            pc_id              = pc_id,
            relationship_delta = _d,
            event_importance   = _imp,
            current_game_time  = _depth_dt,
        )
    except Exception as e:
        print(f"[Updater] 성격 변화 실패 (무시): {e}")

    # ── 임신 체크 ────────────────────────────────────────────
    try:
        return await process_ejaculation(npc_id, actor_response)
    except Exception as e:
        print(f"[PregnancyMgr] 처리 실패 (무시): {e}")
    return None


async def _apply_relationship_status(char_a: str, char_b: str, new_status: str) -> None:
    """RELATIONSHIP 양방향 current_status를 갱신한다."""
    async with async_driver.session() as session:
        for a, b in [(char_a, char_b), (char_b, char_a)]:
            await session.run("""
                MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
                SET r.current_status = $status
            """, a=a, b=b, status=new_status)
    print(f"[Updater] relationship status updated: {char_a} ↔ {char_b}")


# ════════════════════════════════════════════════════════════
# 시간 업데이트
# ════════════════════════════════════════════════════════════

async def apply_time_updates(
    plan:      dict,
    base_time: datetime,
    pc_id:     str,
    npc_id:    str,
) -> datetime:
    """
    manager_agent에서 계산된 plan을 받아 GlobalState + 캐릭터 DB 반영.

    Returns: 새로운 인게임 datetime
    """
    action_type = plan.get("action_type", "dialogue")

    if action_type == "ooc_jump" and plan.get("target_hour") is not None:
        target_hour = int(plan["target_hour"])
        days_to_add = 1 if target_hour <= base_time.hour else 0
        new_time    = base_time.replace(hour=target_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_to_add)
    else:
        minutes = plan.get("elapsed_minutes")
        if not isinstance(minutes, int) or not (0 < minutes < 10080):
            minutes = 3
        new_time = base_time + timedelta(minutes=minutes)

    days_passed = (new_time.date() - base_time.date()).days

    update_fields = ["gs.currentTime = $new_time"]
    params: dict  = {"new_time": new_time.isoformat()}

    new_weather = plan.get("new_weather")
    if new_weather and new_weather != "null":
        update_fields.append("gs.weather = $weather")
        params["weather"] = new_weather

    new_loc_id = plan.get("new_location_id")
    if new_loc_id and new_loc_id != "null":
        update_fields.append("gs.currentLocationId = $loc_id")
        params["loc_id"] = new_loc_id

    try:
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET {', '.join(update_fields)}",
                **params,
            )

        if days_passed > 0:
            for char_id in (pc_id, npc_id):
                await advance_cycle_day(char_id, days_passed)

        if new_loc_id and new_loc_id != "null":
            for char_id in (pc_id, npc_id):
                await move_location(char_id, new_loc_id)

        print(f"[TimeManager] {new_time.strftime('%Y-%m-%d %H:%M')} | {plan.get('reason', '')}")

    except Exception as e:
        print(f"[TimeManager] DB 업데이트 실패: {e}")

    return new_time


# ════════════════════════════════════════════════════════════
# event_only 경로 (OOC 트리거 전용)
# ════════════════════════════════════════════════════════════

async def _get_current_iso_time() -> str:
    """GlobalState에서 현재 ISO 시간 문자열을 반환한다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await rec.single()
        if row and row["ct"]:
            return row["ct"]
    return datetime.now().isoformat()


async def _generate_event_plan(
    input_text:      str,
    npc_id:          str,
    pc_id:           str,
    initial_changes: dict,
) -> dict:
    """
    event_only 경로용 LLM 플랜 생성.
    DynamicState / 호감도 없이 Event 생성 여부와 relationship_status만 판단한다.
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    system_instruction = f"You are a precise event recorder for a roleplay system. Focus on {npc_id} and {pc_id}."

    prompt = f"""Initial state changes detected: {json.dumps(initial_changes, ensure_ascii=False)}

Decide whether to create an Event node based on the scene below.

## Event Importance
9–10: Life-altering (hospitalization, surgery, serious accident, first confession)
6–8: Significant (major injury, near-breakup, first intimacy)
3–5: Noteworthy (first clinic visit for new injury, minor conflict)
0–2: DO NOT create (routine, follow-up visit)

ID format: {{location}}_{{description}}_{{YYYYMMDD_HHMM}}

When importance ≥ 7 add "new_relationship_status" to new_event:
  1–3 sentences English describing the current relationship state after this event.

Return ONLY valid JSON:
{{
  "new_event": null
}}

Scene / OOC command:
{input_text[:1500]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
    )

    plan = extract_json_from_llm(response.text, source="event_plan")
    if not isinstance(plan, dict):
        plan = {}
    return plan


async def _update_acceptance_scores(npc_id: str, ts_delta: int, na_delta: int) -> None:
    """ts_acceptance / northern_attachment 수치를 delta 적용 후 저장."""
    if not ts_delta and not na_delta:
        return

    async with async_driver.session() as session:
        await session.run(
            """
            MATCH (c:Character {id: $npc_id})-[:HAS_STATE]->(s:DynamicState)
            WHERE s.ts_acceptance IS NOT NULL
            SET s.ts_acceptance       = max(0, min(100,
                    coalesce(s.ts_acceptance, 0) + $ts_delta)),
                s.northern_attachment = max(0, min(100,
                    coalesce(s.northern_attachment, 0) + $na_delta))
            """,
            npc_id=npc_id, ts_delta=ts_delta, na_delta=na_delta,
        )

    if ts_delta:
        print(f"[AcceptanceUpdater] {npc_id}: ts_acceptance +{ts_delta}")
    if na_delta:
        print(f"[AcceptanceUpdater] {npc_id}: northern_attachment +{na_delta}")


async def _create_event(event_data: dict, npc_id: str, pc_id: str) -> None:
    """Event 노드 생성 + Memory 노드 생성까지 처리한다."""
    if not event_data or not event_data.get("id"):
        return

    timestamp_fmt = await get_in_universe_time()
    timestamp_iso = await _get_current_iso_time()
    summary       = event_data.get("summary", "")
    importance    = event_data.get("importance", 5)

    embedding = None
    if summary:
        try:
            embedding = await embed_async(summary)
        except Exception as e:
            print(f"[Updater] 임베딩 생성 실패 (무시): {e}")

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Event {
                id:            $id,
                summary:       $summary,
                timestamp:     $timestamp,
                importance:    $importance,
                impact:        $impact,
                decay_rate:    0.1,
                summary_level: 0,
                embedding:     $embedding
            })
        """,
            id=event_data["id"], summary=summary, timestamp=timestamp_fmt,
            importance=importance, impact=event_data.get("impact", ""),
            embedding=embedding,
        )

        for char_id in [npc_id, pc_id]:
            await session.run("""
                MATCH (c:Character {id: $char_id})
                MATCH (e:Event {id: $event_id})
                CREATE (c)-[:INVOLVED_IN]->(e)
            """, char_id=char_id, event_id=event_data["id"])

        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.shared_events    = coalesce(r.shared_events, []) + [$event_id],
                r.last_interaction = $timestamp
        """, a=npc_id, b=pc_id, event_id=event_data["id"], timestamp=timestamp_fmt)

    print(f"[Updater] event created: {event_data['id']} (importance={importance})")

    await ensure_memories_for_event(
        event_id   = event_data["id"],
        summary    = summary,
        importance = importance,
        char_ids   = [npc_id, pc_id],
        timestamp  = timestamp_iso,
        embedding  = embedding,
    )


async def delegate_complex_update(
    actor_response:  str,
    npc_id:          str,
    pc_id:           str,
    initial_changes: dict | None = None,
    event_only:      bool = False,
    world_config:    dict | None = None,
    scene_chars:     list[str] | None = None,
) -> None:
    """
    event_only=True 전용 경로. OOC 트리거(부상·입원)에서만 호출.
    Event 생성 + relationship_status 갱신만 수행한다.
    DynamicState / 호감도 업데이트는 포함하지 않는다.
    """
    from src.simulation.systems.social import resolve_and_update as wb_resolve

    plan = await _generate_event_plan(
        input_text      = actor_response,
        npc_id          = npc_id,
        pc_id           = pc_id,
        initial_changes = initial_changes or {},
    )

    new_event = plan.get("new_event")
    if new_event:
        await _create_event(new_event, npc_id, pc_id)
        new_status = new_event.get("new_relationship_status")
        if new_status and new_event.get("importance", 0) >= 7:
            await _apply_relationship_status(npc_id, pc_id, new_status)

    if world_config and scene_chars:
        try:
            await wb_resolve(
                char_names       = scene_chars,
                main_npc_id      = npc_id,
                pc_id            = pc_id,
                world_config     = world_config,
                event_id         = new_event.get("id") if new_event else None,
                event_importance = new_event.get("importance", 0) if new_event else 0,
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")
