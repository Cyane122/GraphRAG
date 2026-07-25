# ================================
# src/simulation/systems/world_dynamics/organic_models.py
#
# Graph와 Wiki가 저장소 의존성 없이 공유하는 생식 확률 규칙을 정의합니다.
#
# Functions
#   - calculate_pregnancy_probability(cycle_day: int, count: int) -> float : 가임 주기와 누적 횟수로 임신 확률을 계산합니다.
# ================================

from __future__ import annotations

BASE_FERTILE = 0.27
BASE_INFERTILE = 0.01
PROB_CAP = 0.45

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


def calculate_pregnancy_probability(cycle_day: int, count: int) -> float:
    """가임 주기와 현재 주기 누적 횟수로 상한이 있는 임신 확률을 반환합니다."""
    if 10 <= cycle_day <= 17:
        base = BASE_FERTILE * DAY_WEIGHT.get(cycle_day, 0.1)
    else:
        base = BASE_INFERTILE
    return min(1 - (1 - base) ** count, PROB_CAP)
