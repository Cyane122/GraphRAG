# ================================
# src/simulation/state/audit.py
#
# Validate Actor response state, relationship, and event update candidates.
#
# Functions
#   - guard_actor_response(actor_response: str, npc_id: str, pc_id: str, world_config: dict | None) -> dict : Rule-based guard before DB writes
#   - _needs_classification(actor_response: str, scene_types: list[str]) -> bool : Decide whether LLM classification is needed
#   - _audit_state_updates(state: dict, actor_response: str, npc_id: str) -> tuple[dict, list[dict]] : Validate state diffs
#   - _audit_relationship_delta(delta: object, actor_response: str, npc_id: str, pc_id: str) -> tuple[int | None, dict | None] : Validate relationship deltas
#   - _clamp_relationship_delta(delta: int, actor_response: str) -> int : Limit per-turn affinity movement
#   - _audit_event_candidate(new_event: object, actor_response: str) -> tuple[dict | None, dict | None] : Validate event candidates
#   - _audit_time_location_schedule(manager_effects: dict | None) -> dict : Validate time/location/schedule feasibility signals
#   - _write_state_audit_snapshot(actor_response: str, npc_id: str, pc_id: str, guard: dict, state_candidates: list[dict] | None, relationship_candidate: dict | None, event_candidate: dict | None, feasibility_audit: dict | None) -> None : Save an audit snapshot
# ================================
import json
import re
from datetime import datetime
from pathlib import Path

from src.core.state_normalization import normalize_stress_level

# ════════════════════════════════════════════════════════════
# 분류 게이트
# ════════════════════════════════════════════════════════════

_CHANGE_PATTERN = re.compile(
    r"다쳤|부상|병원|골절|삐었|쓰러|기절|아프|열이|입원|"
    r"이동했|나갔|도착|들어왔|장소|"
    r"스트레스|화났|슬퍼|불안|우울|힘들|짜증|무너|피곤|지쳐|헉헉|숨이\s*차|땀|"
    r"싸웠|화해|고백|사귀|헤어|"
    r"injured|hospitalized|arrived|moved|stressed|fatigued|tired"
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
    r"피곤|지쳐|헉헉|숨이\s*차|숨을\s*몰아|땀|"
    r"injured|wound|bruise|hospital|fever|pain|fatigued|tired)",
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
_STATE_AUDIT_DIR = Path("logs/state_audit")
_RELATIONSHIP_ROUTINE_DELTA_CAP = 2
_RELATIONSHIP_MEANINGFUL_DELTA_CAP = 5
_RELATIONSHIP_MILESTONE_DELTA_CAP = 10
_RELATIONSHIP_MILESTONE_RE = re.compile(
    r"(confess|confession|reconcile|reconciliation|breakup|betray|saved|rescue|"
    r"first intimacy|near-death|life-saving|고백|화해|이별|배신|구해|구했다|구해줬|"
    r"목숨|첫 관계|처음으로|결정적|돌이킬 수)",
    re.IGNORECASE,
)


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
    return normalize_stress_level(value)


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


def _normalize_memory_type(value: object, summary: str, impact: str, importance: int) -> str:
    """Return one of the first-pass long-term memory types."""
    if isinstance(value, str) and value.strip().lower() in {"episodic", "emotional", "relational"}:
        return value.strip().lower()

    text = f"{summary}\n{impact}".lower()
    if importance >= 7 or re.search(r"confess|reconcile|breakup|trust|affinity|관계|고백|화해|갈등|신뢰", text):
        return "relational"
    if re.search(r"fear|hurt|relief|shame|anxious|sad|angry|감정|불안|상처|안도|분노|슬픔", text):
        return "emotional"
    return "episodic"


def _prepare_event_summaries(event_data: dict) -> dict:
    """Fill Event summary roles without requiring another LLM call."""
    summary = str(event_data.get("summary") or "").strip()
    impact = str(event_data.get("impact") or "").strip()
    importance = max(0, min(10, _safe_int(event_data.get("importance"), 0)))
    memory_type = _normalize_memory_type(event_data.get("memory_type"), summary, impact, importance)
    narrative_summary = str(event_data.get("narrative_summary") or summary).strip()
    state_summary = str(event_data.get("state_summary") or impact or summary).strip()
    return {
        "summary": summary,
        "impact": impact,
        "importance": importance,
        "memory_type": memory_type,
        "narrative_summary": narrative_summary,
        "state_summary": state_summary,
    }


def _audit_state_updates(state: dict, actor_response: str, npc_id: str) -> tuple[dict, list[dict]]:
    """DynamicState 후보에 confidence/evidence를 붙이고 commit 가능한 필드만 반환합니다."""
    accepted: dict = {}
    candidates: list[dict] = []

    for field, value in state.items():
        evidence = _find_evidence(actor_response, field, value)
        confidence = 0.85 if evidence else 0.45
        policy = "commit" if confidence >= _COMMIT_CONFIDENCE and evidence else "hold"

        if field == "location_id" and value:
            confidence = max(confidence, 0.75)
            evidence = evidence or "location transition inferred from scene"
            policy = "commit"

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
    value = _clamp_relationship_delta(int(delta), actor_response)
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


def _clamp_relationship_delta(delta: int, actor_response: str) -> int:
    """Limit per-turn affinity movement before it reaches the graph."""
    abs_delta = abs(delta)
    if abs_delta <= _RELATIONSHIP_ROUTINE_DELTA_CAP:
        return delta

    cap = (
        _RELATIONSHIP_MILESTONE_DELTA_CAP
        if _RELATIONSHIP_MILESTONE_RE.search(actor_response or "")
        else _RELATIONSHIP_MEANINGFUL_DELTA_CAP
    )
    if abs_delta <= cap:
        return delta
    return cap if delta > 0 else -cap


def _audit_event_candidate(new_event: object, actor_response: str) -> tuple[dict | None, dict | None]:
    """Event 후보를 감사하고 commit 여부를 결정합니다."""
    if not isinstance(new_event, dict):
        return None, None
    importance = max(0, min(10, _safe_int(new_event.get("importance"), 0)))
    summary = str(new_event.get("summary") or "").strip()
    evidence = summary or actor_response[:220].strip()
    confidence = 0.8 if importance >= 2 and summary else 0.4
    policy = "commit" if confidence >= _COMMIT_CONFIDENCE and importance >= 2 and summary else "hold"
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


def _audit_time_location_schedule(manager_effects: dict | None) -> dict:
    """Return lightweight feasibility audit signals for time and location planning."""
    effects = manager_effects or {}
    time_plan = effects.get("time_plan") or {}
    context_plan = effects.get("context_plan") or {}
    issues: list[dict] = []

    elapsed = _safe_int(time_plan.get("elapsed_minutes"), 0)
    new_location_id = time_plan.get("new_location_id")
    action_type = str(time_plan.get("action_type") or "")

    if new_location_id and 0 < elapsed < 2:
        issues.append(_issue(
            "fast_location_transition",
            "warning",
            f"new_location_id={new_location_id}, elapsed_minutes={elapsed}",
        ))

    if action_type == "ooc_jump":
        issues.append(_issue(
            "ooc_time_jump",
            "warning",
            str(time_plan.get("reason") or "ooc time jump"),
        ))

    required_systems = set(context_plan.get("required_systems") or [])
    if "goals" in required_systems and not context_plan.get("priority_order"):
        issues.append(_issue(
            "missing_context_priority_order",
            "warning",
            "long-term pressure requested without explicit priority_order",
        ))

    return {
        "passed": not any(item["severity"] == "reject" for item in issues),
        "issues": issues,
        "time_plan": {
            "action_type": action_type,
            "elapsed_minutes": time_plan.get("elapsed_minutes"),
            "new_location_id": new_location_id,
            "new_time": time_plan.get("new_time"),
        },
        "context_priority_order": context_plan.get("priority_order") or [],
    }


def _write_state_audit_snapshot(
    actor_response: str,
    npc_id: str,
    pc_id: str,
    guard: dict,
    state_candidates: list[dict] | None = None,
    relationship_candidate: dict | None = None,
    event_candidate: dict | None = None,
    feasibility_audit: dict | None = None,
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
            "feasibility_audit": feasibility_audit or {},
            "actor_response_preview": actor_response[:1200],
        }
        (_STATE_AUDIT_DIR / f"{stamp}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[StateAudit] 저장 실패: {e}")
