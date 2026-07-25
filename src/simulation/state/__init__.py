# ================================
# src/simulation/state/__init__.py
#
# Graph/Wiki 공용 accepted-turn Updater와 상태 보조 API를 제공합니다.
#
# Classes
#   - GraphTurnUpdateRequest : Graph 상태 반영 요청
#   - WikiTurnUpdateRequest : Wiki commit 계획·보류 요청
#   - TurnUpdateResult : mode-aware 상태 반영 결과
#
# Functions
#   - update_accepted_turn(request: GraphTurnUpdateRequest | WikiTurnUpdateRequest) -> TurnUpdateResult : mode에 맞는 상태 반영을 실행합니다.
# ================================

from src.simulation.state.models import (
    GraphTurnUpdateRequest,
    TurnUpdateResult,
    WikiTurnUpdateRequest,
)
from src.simulation.state.updater import update_accepted_turn

__all__ = [
    "GraphTurnUpdateRequest",
    "TurnUpdateResult",
    "WikiTurnUpdateRequest",
    "update_accepted_turn",
]
