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
#
# 관계 깊이 파이프라인 (process_actor_response 내부에서 호출):
#   - reputation.propagate_gossip : 중요 이벤트 후 주변 NPC에게 소문 전파
#   - memory.distort_on_affinity_change : 호감도 급변 시 공유 기억 즉시 재해석
#   - personality.check_personality_drift : micro / macro 성격 변화 체크
# ================================

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

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

_STATE_AUDIT_DIR = Path("logs/state_audit")


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
_SYSTEM_LEAK_RE = re.compile(
    r"(system prompt|developer message|as an ai|language model|cannot comply|policy|"
    r"시스템\s*프롬프트|개발자\s*메시지|AI\s*언어\s*모델|정책상)",
    re.IGNORECASE,
)
_SECRET_LEAK_RE = re.compile(
    r"(private_summary|secret_hints|숨겨진\s*설정|비밀은\s*사실|진짜\s*비밀|"
    r"아직\s*밝혀지지\s*않은\s*비밀)",
    re.IGNORECASE,
)
_PC_CONTROL_VERBS = (
    "말했다", "대답했다", "생각했다", "느꼈다", "결심했다", "움직였다",
    "걸었다", "다가갔다", "손을 뻗", "입을 열", "고개를 끄덕", "웃었다", "울었다",
)
_PC_CONTROL_EN_RE = re.compile(
    r"\b(you|the player|pc)\b.{0,80}\b(said|thought|felt|decided|walked|moved|smiled|cried)\b",
    re.IGNORECASE | re.DOTALL,
)
_FIGURATIVE_PHYSICAL_RE = re.compile(
    r"(심장[이가]?\s*터질|가슴[이가]?\s*찢|녹아내리|무너져내리|죽을 것 같|"
    r"숨이\s*막히|피가\s*식|얼어붙)",
)
_PHYSICAL_EVIDENCE_RE = re.compile(
    r"(다쳤|부상|골절|삐었|피가|멍이|상처|입원|병원|열이|기침|토했|통증|아프|"
    r"injured|wound|bruise|hospital|fever|pain)",
    re.IGNORECASE,
)
_FIELD_EVIDENCE: dict[str, re.Pattern] = {
    "mood": re.compile(r"(웃|미소|화[가를]?|짜증|불안|떨|울|눈물|기뻐|행복|긴장|초조)"),
    "mental_condition": re.compile(r"(불안|우울|혼란|초조|무너|지쳐|탈진|스트레스|긴장)"),
    "stress_level": re.compile(r"(스트레스|압박|긴장|불안|초조|버겁|힘들|무너)"),
    "workplace_stress_level": re.compile(r"(손님|직장|업무|상사|동료|근무|가게|회사|스트레스)"),
    "outfit": re.compile(r"(입고|걸치|벗|옷|코트|셔츠|치마|바지|드레스|잠옷)"),
    "injury_marks": _PHYSICAL_EVIDENCE_RE,
    "physical_condition": _PHYSICAL_EVIDENCE_RE,
    "injury_detail": _PHYSICAL_EVIDENCE_RE,
}
_COMMIT_CONFIDENCE = 0.60


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


def _severity_rank(severity: str) -> int:
    """Guard severity 문자열을 정렬 가능한 숫자로 변환합니다."""
    return {"none": 0, "warning": 1, "hold": 2, "reject": 3}.get(severity, 0)


def _issue(code: str, severity: str, evidence: str) -> dict:
    """Guard issue 구조를 생성합니다."""
    return {"code": code, "severity": severity, "evidence": evidence[:220]}


def _extract_sentences(text: str) -> list[str]:
    """응답을 짧은 evidence 후보 문장으로 나눕니다."""
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _find_evidence(text: str, field: str, value: object = None) -> str:
    """상태 변경 후보를 뒷받침하는 짧은 evidence를 찾습니다."""
    sentences = _extract_sentences(text)
    if isinstance(value, str) and value:
        value_l = value.lower()
        for sentence in sentences:
            if value_l in sentence.lower():
                return sentence[:240]

    pattern = _FIELD_EVIDENCE.get(field)
    if pattern:
        for sentence in sentences:
            if pattern.search(sentence):
                return sentence[:240]

    return ""


def guard_actor_response(
    actor_response: str,
    npc_id: str,
    pc_id: str,
    world_config: dict | None = None,
) -> dict:
    """
    Actor 응답이 DB 후처리로 넘어가도 되는지 rule-based로 검사합니다.

    이 guard는 산문 품질 평가가 아니라 DB 오염 방지 장치입니다. reject 이슈가
    하나라도 있으면 State Updater, Event, Memory, 후속 시스템을 실행하지 않습니다.
    """
    issues: list[dict] = []
    text = actor_response or ""

    if _SYSTEM_LEAK_RE.search(text):
        issues.append(_issue("system_leak", "reject", _SYSTEM_LEAK_RE.search(text).group(0)))

    if _SECRET_LEAK_RE.search(text):
        issues.append(_issue("secret_overexposure", "reject", _SECRET_LEAK_RE.search(text).group(0)))

    pc_tokens = {str(world_config.get("pc_name", "")) if world_config else ""}
    pc_tokens.update({"당신", "너"})
    for token in {t for t in pc_tokens if t}:
        for verb in _PC_CONTROL_VERBS:
            m = re.search(rf"{re.escape(token)}(?:은|는|이|가)[^.!?\n]{{0,60}}{verb}", text)
            if m:
                issues.append(_issue("pc_control", "reject", m.group(0)))
                break
        if any(i["code"] == "pc_control" for i in issues):
            break

    m_en = _PC_CONTROL_EN_RE.search(text)
    if m_en:
        issues.append(_issue("pc_control", "reject", m_en.group(0)))

    if _FIGURATIVE_PHYSICAL_RE.search(text) and not _PHYSICAL_EVIDENCE_RE.search(text):
        issues.append(_issue("figurative_physical_risk", "warning", _FIGURATIVE_PHYSICAL_RE.search(text).group(0)))

    severity = "none"
    for item in issues:
        if _severity_rank(item["severity"]) > _severity_rank(severity):
            severity = item["severity"]

    return {
        "passed": severity != "reject",
        "issues": issues,
        "severity": severity,
    }


def _candidate(
    target: str,
    field: str,
    new_value: object,
    confidence: float,
    evidence: str,
    commit_policy: str,
) -> dict:
    """State diff 후보 구조를 생성합니다."""
    return {
        "target": target,
        "field": field,
        "new_value": new_value,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "commit_policy": commit_policy,
    }


def _safe_int(value: object, default: int = 0) -> int:
    """LLM/DB 값을 int로 안전하게 변환합니다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _audit_state_updates(state: dict, actor_response: str, npc_id: str) -> tuple[dict, list[dict]]:
    """DynamicState 후보에 confidence/evidence를 붙이고 commit 가능한 필드만 반환합니다."""
    accepted: dict = {}
    candidates: list[dict] = []

    for field, value in state.items():
        evidence = _find_evidence(actor_response, field, value)
        confidence = 0.85 if evidence else 0.45
        policy = "commit" if confidence >= _COMMIT_CONFIDENCE and evidence else "hold"

        if field in {"physical_condition", "injury_detail", "injury_marks"}:
            if _FIGURATIVE_PHYSICAL_RE.search(actor_response) and not _PHYSICAL_EVIDENCE_RE.search(actor_response):
                confidence = 0.2
                policy = "reject"
                evidence = evidence or _FIGURATIVE_PHYSICAL_RE.search(actor_response).group(0)
            elif not _PHYSICAL_EVIDENCE_RE.search(actor_response):
                confidence = min(confidence, 0.5)
                policy = "hold"

        candidates.append(_candidate(
            target=f"Character:{npc_id}/DynamicState",
            field=field,
            new_value=value,
            confidence=confidence,
            evidence=evidence,
            commit_policy=policy,
        ))
        if policy == "commit":
            accepted[field] = value

    return accepted, candidates


def _audit_relationship_delta(delta: object, actor_response: str, npc_id: str, pc_id: str) -> tuple[int | None, dict | None]:
    """관계 delta 후보를 감사하고 commit 여부를 결정합니다."""
    if not isinstance(delta, (int, float)) or int(delta) == 0:
        return None, None
    evidence = _find_evidence(actor_response, "mood") or actor_response[:180].strip()
    confidence = 0.7 if evidence else 0.45
    policy = "commit" if confidence >= _COMMIT_CONFIDENCE and evidence else "hold"
    value = int(delta)
    return (
        value if policy == "commit" else None,
        _candidate(
            target=f"Relationship:{npc_id}->{pc_id}",
            field="affinity",
            new_value=value,
            confidence=confidence,
            evidence=evidence,
            commit_policy=policy,
        ),
    )


def _audit_event_candidate(new_event: object, actor_response: str) -> tuple[dict | None, dict | None]:
    """Event 후보를 감사하고 commit 여부를 결정합니다."""
    if not isinstance(new_event, dict):
        return None, None
    importance = _safe_int(new_event.get("importance"), 0)
    summary = str(new_event.get("summary") or "").strip()
    evidence = summary or actor_response[:220].strip()
    confidence = 0.8 if importance >= 3 and summary else 0.4
    policy = "commit" if confidence >= _COMMIT_CONFIDENCE and importance >= 3 and summary else "hold"
    return (
        new_event if policy == "commit" else None,
        _candidate(
            target="Event",
            field="new_event",
            new_value=new_event.get("id"),
            confidence=confidence,
            evidence=evidence,
            commit_policy=policy,
        ),
    )


def _write_state_audit_snapshot(
    actor_response: str,
    npc_id: str,
    pc_id: str,
    guard: dict,
    state_candidates: list[dict] | None = None,
    relationship_candidate: dict | None = None,
    event_candidate: dict | None = None,
) -> None:
    """Guard와 StateDiff 후보를 턴별 JSON 로그로 저장합니다."""
    try:
        _STATE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        payload = {
            "timestamp": stamp,
            "npc_id": npc_id,
            "pc_id": pc_id,
            "guard": guard,
            "state_candidates": state_candidates or [],
            "relationship_candidate": relationship_candidate,
            "event_candidate": event_candidate,
            "actor_response_preview": actor_response[:1200],
        }
        (_STATE_AUDIT_DIR / f"{stamp}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[StateAudit] 저장 실패: {e}")


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
            int(plan.get("ts_acceptance_delta") or 0),
            int(plan.get("northern_attachment_delta") or 0),
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

def build_time_plan(plan: dict, base_time: datetime) -> dict:
    """시간/날씨/위치 변경 계획을 DB write 없이 계산합니다."""
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

    elapsed_minutes = max(1.0, (new_time - base_time).total_seconds() / 60)
    new_weather = plan.get("new_weather")
    new_loc_id = plan.get("new_location_id")

    return {
        "action_type":      action_type,
        "base_time":        base_time.isoformat(),
        "new_time":         new_time.isoformat(),
        "elapsed_minutes":  elapsed_minutes,
        "days_passed":      (new_time.date() - base_time.date()).days,
        "new_weather":      new_weather if new_weather and new_weather != "null" else None,
        "new_location_id":  new_loc_id if new_loc_id and new_loc_id != "null" else None,
        "reason":           plan.get("reason", ""),
    }


async def commit_time_plan(time_plan: dict, pc_id: str, npc_id: str) -> datetime:
    """계산된 시간 계획을 GlobalState와 위치 관계에 확정 반영합니다."""
    new_time = datetime.fromisoformat(time_plan["new_time"])

    update_fields = ["gs.currentTime = $new_time"]
    params: dict  = {"new_time": new_time.isoformat()}

    if time_plan.get("new_weather"):
        update_fields.append("gs.weather = $weather")
        params["weather"] = time_plan["new_weather"]

    new_loc_id = time_plan.get("new_location_id")
    if new_loc_id:
        update_fields.append("gs.currentLocationId = $loc_id")
        params["loc_id"] = new_loc_id

    try:
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET {', '.join(update_fields)}",
                **params,
            )

        if int(time_plan.get("days_passed") or 0) > 0:
            for char_id in (pc_id, npc_id):
                await advance_cycle_day(char_id, int(time_plan["days_passed"]))

        if new_loc_id:
            for char_id in (pc_id, npc_id):
                await move_location(char_id, new_loc_id)

        print(f"[TimeManager] {new_time.strftime('%Y-%m-%d %H:%M')} | {time_plan.get('reason', '')}")

    except Exception as e:
        print(f"[TimeManager] DB 업데이트 실패: {e}")

    return new_time


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
    time_plan = build_time_plan(plan, base_time)
    return await commit_time_plan(time_plan, pc_id, npc_id)


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
            SET s.ts_acceptance =
                    CASE
                        WHEN coalesce(s.ts_acceptance, 0) + $ts_delta > 100 THEN 100
                        WHEN coalesce(s.ts_acceptance, 0) + $ts_delta < 0 THEN 0
                        ELSE coalesce(s.ts_acceptance, 0) + $ts_delta
                    END,
                s.northern_attachment =
                    CASE
                        WHEN coalesce(s.northern_attachment, 0) + $na_delta > 100 THEN 100
                        WHEN coalesce(s.northern_attachment, 0) + $na_delta < 0 THEN 0
                        ELSE coalesce(s.northern_attachment, 0) + $na_delta
                    END
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
