# ================================
# src/simulation/systems/needs/math.py
#
# Need value, overflow, multiplier, and libido hint calculations.
#
# Functions
#   - _count_overflows(old_val: float, elapsed_min: float, effective_rate: float) -> tuple[int, float] : Count threshold overflows
#   - _as_float(value: object, default: float = 0.0) -> float : Convert to float
#   - _as_int(value: object, default: int = 0) -> int : Convert to int
#   - _calc_multiplier(need: str, traits: dict, needs: dict, profile: dict) -> float : Calculate need-specific trait multiplier
#   - _build_libido_hint(npc_id: str, profile: dict, needs: dict, traits: dict) -> str | None : Build a libido prompt hint
# ================================
from typing import Optional

from src.simulation.state.audit import _sanitize_stress_level

THRESHOLD = 0.8

NEED_BASE_RATES: dict[str, float] = {
    "hunger": 0.0033,
    "rest": 0.0011,
    "social": 0.00035,
    "fun": 0.00069,
    "safety": 0.001,
    "libido": 0.00017,
}

AUTONOMOUS_NEEDS = {"hunger", "rest", "social", "fun"}

def _count_overflows(
    old_val:        float,
    elapsed_min:    float,
    effective_rate: float,
) -> tuple[int, float]:
    """
    elapsed_min 동안 욕구가 THRESHOLD를 몇 번 초과했는지 계산.
    반환: (초과 횟수, 마지막 정산 후 현재 수치 추정값)
    """
    if effective_rate <= 0:
        return 0, old_val

    time_to_first = max(0.0, (THRESHOLD - old_val) / effective_rate)

    if elapsed_min < time_to_first:
        return 0, min(1.0, old_val + effective_rate * elapsed_min)

    remaining_after_first = elapsed_min - time_to_first
    cycle_time            = THRESHOLD / effective_rate
    additional_overflows  = int(remaining_after_first / cycle_time)
    overflows             = 1 + additional_overflows

    time_in_last_cycle = remaining_after_first - additional_overflows * cycle_time
    settle_base        = 0.2
    settled_val        = min(1.0, settle_base + effective_rate * time_in_last_cycle)

    return overflows, settled_val


def _as_float(value, default: float = 0.0) -> float:
    """Nullable DB values and malformed LLM values fall back to a numeric default."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    """Nullable DB values and malformed LLM values fall back to an integer default."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _calc_multiplier(
    need:    str,
    traits:  dict,
    needs:   dict,
    profile: dict,
) -> float:
    """트레이트 + 현재 상태 기반 욕구 증가 속도 multiplier 계산."""
    t = traits

    if need == "hunger":
        m = 1.0
        m += _as_float(t.get("trait_gluttony"), 0.0) * 0.4
        return max(0.3, m)

    elif need == "rest":
        m = 1.0
        m += _as_float(t.get("trait_laziness"), 0.0) * 0.5
        m += _as_float(t.get("trait_vitality"), 0.0) * -0.35
        m += _as_float(t.get("trait_light_sleeper"), 0.0) * 0.3
        physical = needs.get("physical_condition", "healthy")
        if physical in ("injured", "ill", "hospitalized"):
            m *= 1.4
        return max(0.3, m)

    elif need == "social":
        m = 1.0
        m += _as_float(t.get("trait_extroversion"), 0.0) * 1.0
        m += _as_float(t.get("trait_attention_seeking"), 0.0) * 0.6
        m += _as_float(t.get("trait_independence"), 0.0) * -0.4
        return max(0.2, m)

    elif need == "fun":
        m = 1.0
        m += _as_float(t.get("trait_hedonism"), 0.0) * 0.7
        m += _as_float(t.get("trait_curiosity"), 0.0) * 0.4
        stress = _sanitize_stress_level(_as_int(needs.get("stress_level"), 0))
        if stress and stress >= 7:
            m *= 0.5
        return max(0.2, m)

    elif need == "safety":
        m = 1.0
        m += _as_float(t.get("trait_anxiety_prone"), 0.0) * 0.5
        mental = needs.get("mental_condition", "stable")
        if mental in ("stressed", "anxious"):
            m *= 1.3
        return max(0.5, m)

    elif need == "libido":
        m = 1.0
        m += _as_float(t.get("trait_libido_drive"), 0.0) * 1.0
        m += _as_float(t.get("trait_hedonism"), 0.0) * 0.4
        m += _as_float(t.get("trait_intimacy_drive"), 0.0) * 0.3
        cycle_day = _as_int(needs.get("cycle_day"), 0)
        if 12 <= cycle_day <= 16:
            m *= 1.8
        physical = needs.get("physical_condition", "healthy")
        if physical in ("fatigued", "injured"):
            m *= 0.4
        return max(0.1, m)

    return 1.0


# ════════════════════════════════════════════════════════════
# Safety decay
# ════════════════════════════════════════════════════════════


_SCENE_NEED_BEHAVIOR: dict[str, str] = {
    "hunger": (
        "mention food or snacks, let stomach growl, bring up eating — "
        "express through casual dialogue or small physical cue. Do NOT narrate the need explicitly."
    ),
    "rest": (
        "yawn, rub eyes, stretch, lean against something — "
        "subtle fatigue signals woven into body language. Do NOT narrate the need explicitly."
    ),
    "social": (
        "seek more engagement: ask a question, bring up a new topic, move closer — "
        "craving more interaction. Do NOT narrate the need explicitly."
    ),
    "fun": (
        "show mild restlessness: glance at phone, suggest a different activity, fidget — "
        "boredom leaking through small gestures. Do NOT narrate the need explicitly."
    ),
}


def _build_need_hint(npc_id: str, need_name: str) -> str | None:
    """씬 내 NPC의 욕구 오버플로우 시 Actor에 전달할 행동 힌트 반환."""
    behavior = _SCENE_NEED_BEHAVIOR.get(need_name)
    if not behavior:
        return None
    return (
        f"[NEEDS_HINT:{npc_id}] {need_name.capitalize()} 0.8+. "
        f"Behavior hint: {behavior}"
    )


def _build_libido_hint(
    npc_id:  str,
    profile: dict,
    needs:   dict,
    traits:  dict,
) -> Optional[str]:
    """
    Libido 0.8 초과 시 Actor 프롬프트에 주입할 hint 문자열 반환.
    행동 이벤트 생성 없음.
    """
    tendency    = profile.get("sexual_tendency", [])
    location_id = needs.get("location_id", "")
    partner_id  = profile.get("libido_partner", "")

    if "repressed" in tendency:
        return (
            f"[NEEDS_HINT:{npc_id}] Libido is suppressed — "
            "increases sensitivity and visible tension. Do NOT depict resolution."
        )

    if "villa" in location_id or "home" in location_id or "205" in location_id:
        privacy = "private"
    elif "bathroom" in location_id or "restroom" in location_id:
        privacy = "semi-private"
    else:
        privacy = "public"

    has_partner = bool(partner_id)

    if privacy == "private":
        hint = (
            "initiate_intimacy — body language: lingering gaze, casual touch, proximity"
            if has_partner
            else "solo_relief — brief withdrawal, sounds from another room"
        )
    elif privacy == "semi-private":
        if "exhibitionism" in tendency or "light_exhibitionism" in tendency:
            hint = "exhibitionism_urge — small daring gesture, checking if observed"
        else:
            hint = "seek_private_space — restless, distracted, excuses self"
    else:
        if "exhibitionism" in tendency:
            hint = "exhibitionism_urge — subtle but deliberate exposure gesture"
        elif has_partner:
            hint = "suppress + think_of_partner — distracted eye contact / brief touch"
        else:
            hint = "suppress — heightened sensory awareness, brief distraction"

    return (
        f"[NEEDS_HINT:{npc_id}] Libido 0.8+. "
        f"Behavior hint: {hint}. Do NOT narrate the need explicitly."
    )


# ════════════════════════════════════════════════════════════
# DB 읽기 / 쓰기
# ════════════════════════════════════════════════════════════
