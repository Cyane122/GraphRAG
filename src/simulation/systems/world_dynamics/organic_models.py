# ================================
# src/simulation/systems/world_dynamics/organic_models.py
#
# Graph와 Wiki가 저장소 의존성 없이 공유하는 생식 확률 규칙을 정의합니다.
#
# Functions
#   - normalize_contraception_value(value: str | None) -> str : canonical 피임 상태 값을 `none` 또는 `oral`로 정규화합니다.
#   - calculate_pregnancy_probability(cycle_day: int, count: int, contraception: str | None = None) -> float : 가임 주기, 누적 횟수, 피임 상태로 임신 확률을 계산합니다.
# ================================

from __future__ import annotations

BASE_FERTILE = 0.27
BASE_INFERTILE = 0.01
PROB_CAP = 0.45
# Gameplay dial only, not a medical claim: oral contraception keeps a small
# residual chance so the state stays playable and non-zero in long runs.
ORAL_CONTRACEPTION_RISK_MULTIPLIER = 0.10

DAY_WEIGHT: dict[int, float] = {
    10: 0.30,
    11: 0.50,
    12: 0.70,
    13: 0.90,
    14: 1.00,
    15: 0.80,
    16: 0.30,
    17: 0.10,
}


def normalize_contraception_value(value: str | None) -> str:
    """Canonical 피임 상태 값을 `none` 또는 `oral`로 정규화합니다."""
    normalized = str(value or "").strip().lower()
    return "oral" if normalized == "oral" else "none"


def calculate_pregnancy_probability(
    cycle_day: int,
    count: int,
    contraception: str | None = None,
) -> float:
    """가임 주기와 현재 주기 누적 횟수로 상한이 있는 임신 확률을 반환합니다."""
    if 10 <= cycle_day <= 17:
        base = BASE_FERTILE * DAY_WEIGHT.get(cycle_day, 0.1)
    else:
        base = BASE_INFERTILE
    if normalize_contraception_value(contraception) == "oral":
        base *= ORAL_CONTRACEPTION_RISK_MULTIPLIER
    return min(1 - (1 - base) ** count, PROB_CAP)
