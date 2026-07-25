# ================================
# src/simulation/systems/world_dynamics/__init__.py
#
# 장기 세계 동역학 공개 API를 저장소 의존성 없이 지연 노출합니다.
#
# Functions
#   - __getattr__(name: str) -> object : 요청된 공개 함수를 소유 모듈에서 지연 로드합니다.
# ================================

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "tick_cycle_day": ("src.simulation.systems.world_dynamics.organic", "tick_cycle_day"),
    "tick_all_cycles": ("src.simulation.systems.world_dynamics.organic", "tick_all_cycles"),
    "process_ejaculation": (
        "src.simulation.systems.world_dynamics.organic",
        "process_ejaculation",
    ),
    "set_pregnant_manual": (
        "src.simulation.systems.world_dynamics.organic",
        "set_pregnant_manual",
    ),
    "simulate_internal_ejaculation": (
        "src.simulation.systems.world_dynamics.organic",
        "simulate_internal_ejaculation",
    ),
    "check_personality_drift": (
        "src.simulation.systems.world_dynamics.personality",
        "check_personality_drift",
    ),
    "propagate_gossip": (
        "src.simulation.systems.world_dynamics.reputation",
        "propagate_gossip",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> object:
    """요청된 world-dynamics 공개 속성을 소유 모듈에서 한 번 로드해 반환합니다."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
