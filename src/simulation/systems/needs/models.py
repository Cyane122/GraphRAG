# ================================
# src/simulation/systems/needs/models.py
#
# Needs 도메인의 데이터 모델과 기준 상수를 정의합니다.
# 욕구 수치 벡터(6종)의 단일 출처 — 기존에는 agents/resolver.py에 흩어져 있던 상수를
# needs 패키지로 모아 레이어 역방향 import를 없앱니다.
#
# Classes
#   - NeedLevels : NPC 6개 욕구 수치(hunger/rest/social/fun/safety/libido, 0.0~1.0)
#
# (module-level)
#   - NEED_DEFAULTS : dict[str, float] — 신규 NeedsState 기본 수치
#   - NEED_BASE_RATES : dict[str, float] — 분당 기본 욕구 증가율
#   - SETTLE_LEVELS : dict[str, float] — 욕구 해소 직후 안착 수치(safety 제외)
# ================================

from pydantic import BaseModel


class NeedLevels(BaseModel):
    """NPC의 6개 욕구 수치(0.0~1.0). 1.0(threshold)에 가까울수록 해당 욕구가 강하다."""

    hunger: float = 0.3
    rest: float = 0.2
    social: float = 0.1
    fun: float = 0.4
    safety: float = 0.05
    libido: float = 0.2

    def to_dict(self) -> dict[str, float]:
        """6개 욕구 수치를 평범한 dict로 반환한다(dict 기반 기존 코드와의 경계용)."""
        return self.model_dump()


# 신규 NeedsState 노드 생성 시 사용하는 기본값 — NeedLevels 기본값을 단일 출처로 삼는다.
NEED_DEFAULTS: dict[str, float] = NeedLevels().to_dict()

# 특성 보정 전 분당 기본 증가율. Graph와 Wiki의 결정적 시간 경과가 함께 사용한다.
NEED_BASE_RATES: dict[str, float] = {
    "hunger": 0.0033,
    "rest": 0.0011,
    "social": 0.00035,
    "fun": 0.00069,
    "safety": 0.001,
    "libido": 0.00017,
}

# 욕구 해소 직후 안착시키는 기준값(safety는 Event 기반으로 따로 계산하므로 제외).
SETTLE_LEVELS: dict[str, float] = {
    "hunger": 0.15,
    "rest": 0.10,
    "social": 0.20,
    "fun": 0.20,
    "libido": 0.10,
}
