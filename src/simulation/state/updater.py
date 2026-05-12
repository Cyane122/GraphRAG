# ================================
# src/simulation/state/updater.py
#
# 상태 업데이트 파이프라인 전체를 담당합니다.
# Classifier + Complex Updater + Relationship Status를 단일 LLM 호출로 처리합니다.
#
# Functions
#   - process_actor_response(actor_response, npc_id, pc_id, scene_types, scene_chars, world_config) -> str | None : Actor 응답 분석 후 상태 업데이트. 임신 OOC 메시지 반환.
#   - guard_actor_response(actor_response: str, npc_id: str, pc_id: str, world_config: dict | None) -> dict : Actor 응답 DB 반영 전 rule-based guard
#   - build_time_plan(plan: dict, base_time: datetime) -> dict : 시간 계획을 DB write 없이 계산
#   - commit_time_plan(time_plan: dict, pc_id: str, npc_id: str) -> datetime : 계산된 시간 계획을 DB에 반영
#   - apply_time_updates(plan, base_time, pc_id, npc_id) -> datetime : 시간 계획을 DB에 반영
#   - delegate_complex_update(actor_response, npc_id, pc_id, initial_changes, event_only, world_config, scene_chars) -> str | None : event_only 경로 전용 복합 업데이트
#   - _normalize_memory_type(value: object, summary: str, impact: str, importance: int) -> str : 이벤트를 1차 Memory Type으로 정규화
#   - _prepare_event_summaries(event_data: dict) -> dict : Event/Memory summary 역할 필드 보강
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
    get_in_universe_time,
)
from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.embedding.encoder import embed_async
from src.simulation.systems.memory import ensure_memories_for_event
from src.simulation.systems.organic import process_ejaculation
from src.simulation.state.audit import (
    _audit_event_candidate,
    _audit_relationship_delta,
    _audit_state_updates,
    _needs_classification,
    _safe_int,
    _sanitize_stress_level,
    _write_state_audit_snapshot,
    guard_actor_response,
)
from src.simulation.state.events import (
    _apply_relationship_status,
    _create_event,
    _get_current_iso_time,
    _update_acceptance_scores,
    delegate_complex_update,
)
from src.simulation.state.time_plan import (
    apply_time_updates,
    build_time_plan,
    commit_time_plan,
)





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
- stress_level: JSON number from 0 to 10 ONLY. Never return strings like "high", "low", or "5".
  10=가족사망·부정발각, 5=시험실패·심각한다툼, 3=지갑분실·사소한언쟁, 0=완벽한하루
- workplace_stress_level: JSON number from 0 to 10 ONLY. Never return strings like "high", "low", or "5".
  10=해고·공개망신, 6=지속불쾌접촉, 2=까다로운손님, 0=순탄한근무
- outfit: current clothing IF explicitly described (Korean ≤25 chars). Omit entirely if not mentioned.
- injury_marks: "없음" or visible injury description. Only update if changed this scene.

LITERAL only:
- physical_condition: healthy/fatigued/injured/ill/hospitalized
- injury_detail: body part + type (LITERAL events only)
Numeric fields must be JSON numbers, not quoted strings. Correct: {{"dynamic_state": {{"stress_level": 8}}}}. Wrong: {{"dynamic_state": {{"stress_level": "high"}}}}.

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
  "memory_type": "episodic|emotional|relational",
  "narrative_summary": "Actor-facing story continuity, 1 sentence",
  "state_summary": "Fact/state preservation summary, 1 sentence",
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

    guard = guard_actor_response(actor_response, npc_id, pc_id, world_config)
    if not guard["passed"]:
        print(f"[StateGuard] rejected: {json.dumps(guard, ensure_ascii=False)}")
        _write_state_audit_snapshot(actor_response, npc_id, pc_id, guard)
        return None
    if guard["issues"]:
        print(f"[StateGuard] warning: {json.dumps(guard, ensure_ascii=False)}")

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
    state, state_candidates = _audit_state_updates(state, actor_response, npc_id)
    if state_candidates:
        print(f"[StateDiff] {json.dumps(state_candidates, ensure_ascii=False)}")
    if state:
        await update_dynamic_state(npc_id, state)

    # ── 관계 호감도 ──────────────────────────────────────────
    delta, rel_candidate = _audit_relationship_delta(
        plan.get("relationship_delta"), actor_response, npc_id, pc_id
    )
    if rel_candidate:
        print(f"[RelationshipDiff] {json.dumps(rel_candidate, ensure_ascii=False)}")
    if delta:
        d = int(delta)
        await update_relationship_affinity(npc_id, pc_id, d)
        await update_relationship_affinity(pc_id, npc_id, d)

    # ── TS 점수 (세계별 옵션) ────────────────────────────────
    if world_config and world_config.get("ts_scoring_enabled"):
        await _update_acceptance_scores(
            npc_id,
            _safe_int(plan.get("ts_acceptance_delta"), 0),
            _safe_int(plan.get("northern_attachment_delta"), 0),
        )

    # ── 이벤트 생성 + Relationship Status 갱신 ───────────────
    new_event, event_candidate = _audit_event_candidate(plan.get("new_event"), actor_response)
    if event_candidate:
        print(f"[EventDiff] {json.dumps(event_candidate, ensure_ascii=False)}")
    _write_state_audit_snapshot(
        actor_response          = actor_response,
        npc_id                  = npc_id,
        pc_id                   = pc_id,
        guard                   = guard,
        state_candidates        = state_candidates,
        relationship_candidate  = rel_candidate,
        event_candidate         = event_candidate,
    )
    if new_event:
        await _create_event(new_event, npc_id, pc_id)
        new_status = new_event.get("new_relationship_status")
        event_importance = _safe_int(new_event.get("importance"), 0)
        if new_status and event_importance >= 7:
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
                event_importance = _safe_int(new_event.get("importance"), 0) if new_event else 0,
            )
        except Exception as e:
            print(f"[WorldBuilder] resolve 실패 (무시): {e}")

    # ── 관계 깊이 파이프라인 ─────────────────────────────────
    # 소문 전파 / 호감도 급변 왜곡 / 성격 변화. 시간은 한 번만 조회.
    _d        = int(delta or 0)
    _imp      = _safe_int(new_event.get("importance"), 0) if new_event else 0
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
    # Life-depth postprocessors keep long-running goals, meaningful objects,
    # and conditional secrets in step with the accepted actor response.
    _event_id = new_event.get("id") if new_event else None
    try:
        from src.simulation.systems.goals import apply_goal_updates

        await apply_goal_updates(
            actor_response = actor_response,
            owner_id       = npc_id,
            pc_id          = pc_id,
            current_time   = _depth_dt,
            event_id       = _event_id,
        )
    except Exception as e:
        print(f"[LifeDepth] goal update failed (ignored): {e}")

    try:
        from src.simulation.systems.items import apply_item_updates

        await apply_item_updates(
            actor_response = actor_response,
            owner_id       = npc_id,
            pc_id          = pc_id,
            current_time   = _depth_dt,
            event_id       = _event_id,
        )
    except Exception as e:
        print(f"[LifeDepth] item update failed (ignored): {e}")

    try:
        from src.simulation.systems.secrets import apply_secret_updates

        await apply_secret_updates(
            actor_response = actor_response,
            owner_id       = npc_id,
            pc_id          = pc_id,
            current_time   = _depth_dt,
            event_id       = _event_id,
        )
    except Exception as e:
        print(f"[LifeDepth] secret update failed (ignored): {e}")

    try:
        return await process_ejaculation(npc_id, actor_response)
    except Exception as e:
        print(f"[PregnancyMgr] 처리 실패 (무시): {e}")
    return None
