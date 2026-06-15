# ================================
# src/simulation/state/apply/update_policy.py
#
# Post-response update gating policy for accepted Actor responses.
#
# Functions
#   - has_event_signal(actor_response: str, participant_ids: list[str], manager_effects: dict | None, active_event: dict | None = None) -> bool : Return whether event extraction is warranted.
#   - should_run_auxiliary_character_updates(actor_response: str, participant_ids: list[str], context_plan: dict | None = None, world_config: dict | None = None) -> bool : Return whether secondary character extractors should run.
#   - should_run_secondary_relationship_updates(participant_ids: list[str]) -> bool : Return whether secondary relationship updater should run.
#   - should_run_life_depth_system(system_name: str, actor_response: str, context_plan: dict | None, event_importance: int, relationship_delta: int, scene_types: list[str] | None = None) -> bool : Return whether a long-running system should run.
# ================================

from __future__ import annotations

import re

from src.simulation.state.extract.creator_slots import has_dynamic_slot_signal


_ORGANIC_SIGNAL_RE = re.compile(
    r"(질내사정|안에\s*(?:싸|쌌|사정)|속에\s*(?:싸|쌌|사정)|"
    r"콘돔\s*(?:찢|터지|파열)|안에\s*싸도|came\s+inside|cum(?:med)?\s+inside|condom\s*(?:broke|split|tore))",
    re.IGNORECASE,
)


def _safe_importance(context_plan: dict | None) -> int:
    """Read context importance as an integer."""
    try:
        return int((context_plan or {}).get("importance") or 0)
    except (TypeError, ValueError):
        return 0


def _required_systems(context_plan: dict | None) -> set[str]:
    """Return required systems from a context plan."""
    return set((context_plan or {}).get("required_systems") or [])


def _query_focus(context_plan: dict | None) -> set[str]:
    """Return query focus labels from a context plan."""
    return set((context_plan or {}).get("query_focus") or [])


def _has_organic_signal(actor_response: str) -> bool:
    """Return whether the response contains pregnancy-system routing signals."""
    return bool(_ORGANIC_SIGNAL_RE.search(actor_response or ""))


def has_event_signal(
    actor_response: str,
    participant_ids: list[str],
    manager_effects: dict | None,
    active_event: dict | None = None,
) -> bool:
    """Return whether event extraction is warranted."""
    return bool(actor_response.strip())


def should_run_auxiliary_character_updates(
    actor_response: str,
    participant_ids: list[str],
    context_plan: dict | None = None,
    world_config: dict | None = None,
) -> bool:
    """Return whether secondary character extractors should run."""
    if len(participant_ids) >= 3:
        return True
    if _safe_importance(context_plan) >= 7:
        return True
    if has_dynamic_slot_signal(actor_response, world_config):
        return True
    return bool(actor_response.strip() and _required_systems(context_plan))


def should_run_secondary_relationship_updates(participant_ids: list[str]) -> bool:
    """Return whether secondary relationship updater should run."""
    return len(participant_ids) > 2


def should_run_life_depth_system(
    system_name: str,
    actor_response: str,
    context_plan: dict | None,
    event_importance: int,
    relationship_delta: int,
    scene_types: list[str] | None = None,
) -> bool:
    """Return whether a long-running system should run."""
    required = _required_systems(context_plan)
    focus = _query_focus(context_plan)
    scenes = set(scene_types or [])

    if system_name == "goals":
        return bool(actor_response.strip())
    if system_name == "items":
        return "items" in required or "object_memory" in focus
    if system_name == "secrets":
        return bool(actor_response.strip())
    if system_name == "organic":
        return "intimate" in scenes or "physical" in scenes or _has_organic_signal(actor_response)
    if system_name == "personality":
        return event_importance >= 5 or abs(relationship_delta) >= 3
    return False
