# ================================
# src/core/database/__init__.py
#
# Database package public interface.
# Exports the driver and helper functions lazily so tools such as the schema
# builder can import package submodules without opening the active Kuzu store.
#
# Classes
#   - KuzuAsyncDriver : Lazy export for concrete per-session Kuzu drivers.
#   - KuzuRecord : Lazy export for Kuzu row wrappers.
#   - KuzuResult : Lazy export for eager Kuzu result wrappers.
#   - KuzuSession : Lazy export for async Kuzu sessions.
#
# Functions
#   - __getattr__(name: str) : Lazily resolves public database exports.
#   - update_dynamic_information(char_id: str, updates: dict) -> None : Lazy export for DynamicInformation updates.
#   - ensure_location(location_id: str | None, name: str, description: str = "", prompt_hint: str = "", parent_location_id: str | None = None, tags: list[str] | None = None, prompt_priority: int = 8) -> str : Lazy export for Location creation/update.
#   - get_in_universe_time() -> str : Lazy export for GlobalState current time lookup.
# ================================

__all__ = [
    "async_driver",
    "KuzuAsyncDriver",
    "KuzuRecord",
    "KuzuResult",
    "KuzuSession",
    "get_dynamic_state_field_types",
    "update_dynamic_state",
    "update_dynamic_information",
    "ensure_location",
    "ensure_relationship",
    "update_relationship_fields",
    "update_relationship_affinity",
    "move_location",
    "advance_cycle_day",
    "get_in_universe_time",
]


def __getattr__(name: str):
    """Resolve public database exports on first attribute access."""
    if name in {
        "async_driver",
        "KuzuAsyncDriver",
        "KuzuRecord",
        "KuzuResult",
        "KuzuSession",
    }:
        from src.core.database import driver

        return getattr(driver, name)

    if name in {
        "update_dynamic_state",
        "get_dynamic_state_field_types",
        "update_dynamic_information",
        "ensure_location",
        "ensure_relationship",
        "update_relationship_fields",
        "update_relationship_affinity",
        "move_location",
        "advance_cycle_day",
        "get_in_universe_time",
    }:
        from src.core.database import helpers

        return getattr(helpers, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
