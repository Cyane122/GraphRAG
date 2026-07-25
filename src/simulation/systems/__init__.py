# ================================
# src/simulation/systems/__init__.py
#
# 장기 simulation subsystem 공개 API를 지연 로드하는 facade입니다.
#
# Functions
#   - __getattr__(name: str) -> object : 요청된 공개 함수를 소유 subsystem에서 지연 로드합니다.
# ================================

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ensure_memories_for_event": (
        "src.simulation.systems.memory",
        "ensure_memories_for_event",
    ),
    "run_decay": ("src.simulation.systems.memory", "run_decay"),
    "distort_on_affinity_change": (
        "src.simulation.systems.memory",
        "distort_on_affinity_change",
    ),
    "ensure_traits": ("src.simulation.systems.needs", "ensure_traits"),
    "ensure_traits_for_characters": (
        "src.simulation.systems.needs",
        "ensure_traits_for_characters",
    ),
    "run_needs_update": ("src.simulation.systems.needs", "run_needs_update"),
    "tick_cycle_day": (
        "src.simulation.systems.world_dynamics",
        "tick_cycle_day",
    ),
    "tick_all_cycles": (
        "src.simulation.systems.world_dynamics",
        "tick_all_cycles",
    ),
    "process_ejaculation": (
        "src.simulation.systems.world_dynamics",
        "process_ejaculation",
    ),
    "build_world_context": ("src.simulation.systems.social", "build_world_context"),
    "resolve_and_update": ("src.simulation.systems.social", "resolve_and_update"),
    "propagate_gossip": (
        "src.simulation.systems.world_dynamics",
        "propagate_gossip",
    ),
    "check_personality_drift": (
        "src.simulation.systems.world_dynamics",
        "check_personality_drift",
    ),
    "fetch_goal_hints": ("src.simulation.systems.goals", "fetch_goal_hints"),
    "apply_goal_updates": ("src.simulation.systems.goals", "apply_goal_updates"),
    "fetch_secret_hints": ("src.simulation.systems.secrets", "fetch_secret_hints"),
    "apply_secret_updates": (
        "src.simulation.systems.secrets",
        "apply_secret_updates",
    ),
    "fetch_object_memory_hints": (
        "src.simulation.systems.items",
        "fetch_object_memory_hints",
    ),
    "apply_item_updates": ("src.simulation.systems.items", "apply_item_updates"),
    "ensure_default_rooms": (
        "src.simulation.systems.kakao",
        "ensure_default_rooms",
    ),
    "fetch_kakao_context": (
        "src.simulation.systems.kakao",
        "fetch_kakao_context",
    ),
    "fetch_kakao_panel_state": (
        "src.simulation.systems.kakao",
        "fetch_kakao_panel_state",
    ),
    "generate_turn_messages": (
        "src.simulation.systems.kakao",
        "generate_turn_messages",
    ),
    "process_kakao_before_actor": (
        "src.simulation.systems.kakao",
        "process_kakao_before_actor",
    ),
    "commit_kakao_effects": (
        "src.simulation.systems.kakao",
        "commit_kakao_effects",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> object:
    """요청된 subsystem 공개 속성을 소유 모듈에서 한 번 로드해 반환합니다."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
