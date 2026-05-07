# ================================
# src/simulation/events/__init__.py
#
# StaticEvent 생명주기 관리 패키지.
# 조건 기반 이벤트의 상태 평가와 플래그 세팅을 외부에 노출합니다.
#
# Functions
#   - evaluate_all(current_dt: datetime) -> list[dict] : 모든 이벤트를 평가하고 활성 hint 목록을 반환합니다.
#   - set_flag(key: str, value: bool) -> None : GlobalState.flags에 플래그를 세팅합니다.
# ================================

from src.simulation.events.manager import evaluate_all, set_flag

__all__ = ["evaluate_all", "set_flag"]
