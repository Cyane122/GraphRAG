# ================================
# src/simulation/state/audit.py
#
# Validate Actor response state, relationship, and event update candidates.
#
# Functions
#   - guard_actor_response(actor_response: str, npc_id: str, pc_id: str, world_config: dict | None) -> dict : Rule-based guard before DB writes
#   - _needs_classification(actor_response: str, scene_types: list[str]) -> bool : Decide whether LLM classification is needed
#   - _compact_evidence(evidence: object, max_chars: int = _EVIDENCE_MAX_CHARS) -> str : Keep audit evidence short
#   - _extract_scene_header(text: str) -> str : Extract the markdown scene header
#   - _audit_state_updates(state: dict, actor_response: str, npc_id: str) -> tuple[dict, list[dict]] : Validate state diffs
#   - _audit_relationship_delta(delta: object, actor_response: str, npc_id: str, pc_id: str) -> tuple[int | None, dict | None] : Validate relationship deltas
#   - _clamp_relationship_delta(delta: int) -> int : Limit per-turn affinity movement
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
_COMMIT_CONFIDENCE = 0.60
_STATE_AUDIT_DIR = Path("logs/state_audit")
_RELATIONSHIP_ROUTINE_DELTA_CAP = 2
_RELATIONSHIP_MEANINGFUL_DELTA_CAP = 4
_EVIDENCE_MAX_CHARS = 160
_SCENE_HEADER_RE = re.compile(r"^\s*\*\*(?P<header>[^*\n]{1,220})\*\*", re.MULTILINE)


def _needs_classification(actor_response: str, scene_types: list[str]) -> bool:
    """LLM 호출이 필요한지 판단. False면 업데이트 전체 스킵."""
    if any(t in scene_types for t in _ALWAYS_CLASSIFY):
        return True
    return bool(actor_response.strip())


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
    return {"code": code, "severity": severity, "evidence": _compact_evidence(evidence)}


def _compact_evidence(evidence: object, max_chars: int = _EVIDENCE_MAX_CHARS) -> str:
    """Normalize evidence into one short log-friendly line."""
    text = re.sub(r"\s+", " ", str(evidence or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _extract_scene_header(text: str) -> str:
    """Extract the leading markdown scene header when present."""
    match = _SCENE_HEADER_RE.search(text or "")
    if not match:
        return ""
    return _compact_evidence(match.group("header"))


def _extract_sentences(text: str) -> list[str]:
    """응답을 짧은 evidence 후보 문장으로 나눕니다."""
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _find_evidence(text: str, field: str, value: object = None) -> str:
    """상태 변경 후보를 뒷받침하는 짧은 evidence를 찾습니다."""
    if field == "location_id":
        header = _extract_scene_header(text)
        if header:
            return _compact_evidence(f"scene header: {header}")

    sentences = _extract_sentences(text)
    if isinstance(value, str) and value:
        value_l = value.lower()
        for sentence in sentences:
            if value_l in sentence.lower():
                return _compact_evidence(sentence)

    body_sentences = [
        sentence
        for sentence in sentences
        if not _SCENE_HEADER_RE.match(sentence)
    ]
    return _compact_evidence(body_sentences[0] if body_sentences else text)


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
        "evidence": _compact_evidence(evidence),
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

    if importance >= 7:
        return "relational"
    if importance >= 4:
        return "emotional"
    return "episodic"


def _prepare_event_summaries(event_data: dict) -> dict:
    """Normalize Event fields while keeping one canonical summary."""
    summary = str(event_data.get("summary") or "").strip()
    impact = str(event_data.get("impact") or "").strip()
    importance = max(0, min(10, _safe_int(event_data.get("importance"), 0)))
    memory_type = _normalize_memory_type(event_data.get("memory_type"), summary, impact, importance)
    return {
        "summary": summary,
        "impact": impact,
        "importance": importance,
        "memory_type": memory_type,
        "narrative_summary": "",
        "state_summary": "",
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
    value = _clamp_relationship_delta(int(delta))
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


def _clamp_relationship_delta(delta: int) -> int:
    """Limit per-turn affinity movement before it reaches the graph."""
    abs_delta = abs(delta)
    if abs_delta <= _RELATIONSHIP_ROUTINE_DELTA_CAP:
        return delta

    cap = _RELATIONSHIP_MEANINGFUL_DELTA_CAP
    if abs_delta <= cap:
        return delta
    return cap if delta > 0 else -cap


def _audit_event_candidate(new_event: object, actor_response: str) -> tuple[dict | None, dict | None]:
    """Event 후보를 감사하고 commit 여부를 결정합니다."""
    if not isinstance(new_event, dict):
        return None, None
    importance = max(0, min(10, _safe_int(new_event.get("importance"), 0)))
    new_event["importance"] = importance
    summary = str(new_event.get("summary") or "").strip()
    evidence = summary or actor_response[:220].strip()
    confidence = 0.8 if summary else 0.4
    policy = "commit" if confidence >= _COMMIT_CONFIDENCE and summary else "hold"
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
