# ================================
# src/core/database/__init__.py
#
# Database package public interface.
# Exports the driver and helper functions lazily so tools such as the schema
# builder can import package submodules without opening the active Kuzu store.
#
# Functions
#   - __getattr__(name: str) : Lazily resolves public database exports.
# ================================

__all__ = [
    "async_driver",
    "update_dynamic_state",
    "update_relationship_affinity",
    "move_location",
    "advance_cycle_day",
    "get_in_universe_time",
]


def __getattr__(name: str):
    """Resolve public database exports on first attribute access."""
    if name == "async_driver":
        from src.core.database.driver import async_driver

        return async_driver

    if name in {
        "update_dynamic_state",
        "update_relationship_affinity",
        "move_location",
        "advance_cycle_day",
        "get_in_universe_time",
    }:
        from src.core.database import helpers

        return getattr(helpers, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
