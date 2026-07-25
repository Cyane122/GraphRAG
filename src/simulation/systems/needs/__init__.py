# ================================
# src/simulation/systems/needs/__init__.py
#
# Needs 공개 API를 저장소 의존성 없이 지연 노출합니다.
#
# Functions
#   - __getattr__(name: str) -> object : 요청된 needs 공개 함수를 소유 모듈에서 지연 로드합니다.
# ================================

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ensure_traits": ("src.simulation.systems.needs.traits", "ensure_traits"),
    "ensure_traits_for_characters": (
        "src.simulation.systems.needs.traits",
        "ensure_traits_for_characters",
    ),
    "run_needs_update": (
        "src.simulation.systems.needs.engine",
        "run_needs_update",
    ),
    "_build_libido_resolve_context": (
        "src.simulation.systems.needs.engine",
        "_build_libido_resolve_context",
    ),
}

__all__ = [
    "ensure_traits",
    "ensure_traits_for_characters",
    "run_needs_update",
]


def __getattr__(name: str) -> object:
    """요청된 needs 공개 속성을 소유 모듈에서 한 번 로드해 반환합니다."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
