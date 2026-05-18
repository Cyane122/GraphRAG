# ================================
# src/agents/manager/models.py
#
# Shared data bundles for the Manager pipeline.
#
# Classes
#   - ManagerBootstrap : World instance, config, and global state bundle
#   - SceneTimePlan : Scene types and time plan bundle
#   - CoreContext : Graph context bundle for prompt rendering
#   - PromptParts : Fixed, genre, and dynamic prompt bundle
#   - ManagerDependencies : Query and classifier dependencies injected by manager/__init__.py
# ================================
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from src.assets.worlds.base import World

@dataclass
class ManagerBootstrap:
    """World config and base state loaded before turn planning."""

    world: World
    world_config: dict
    global_state: dict


@dataclass
class SceneTimePlan:
    """Scene classification plus side-effect-free time calculation."""

    parse_result: dict
    scene_types: list[str]
    base_time: datetime
    current_dt: datetime
    time_plan: dict
    manager_effects: dict


@dataclass
class CoreContext:
    """Graph records required before dynamic world-context rendering."""

    char_data: dict
    user_data: dict
    relationship: dict
    recent_events: list[dict]
    recall_events: list[dict]
    personal_facts: list[dict]
    memory_conflicts: list[str]
    raw_memories: list[dict]
    location_id: str | None
    location_name: str
    location_nodes: list[dict]
    npcs: list[dict]
    scene_state: dict
    context_plan: dict


@dataclass
class PromptParts:
    """Final prompt segments returned by the manager."""

    fixed: str
    genre: str
    dynamic: str


@dataclass
class ManagerDependencies:
    """Low-level manager dependencies injected from manager/__init__.py to avoid cycles."""

    load_world_instance: Callable[[str | None], World]
    fetch_global_state: Callable[[datetime], Awaitable[dict]]
    try_rule_based: Callable[[str, str], dict | None]
    get_allowed_locations: Callable[[], Awaitable[str]]
    classify_and_parse_time: Callable[
        [str, str, dict, str, dict[str, str] | None, dict | None],
        Awaitable[dict],
    ]
    fetch_character_data: Callable[[str, list[str]], Awaitable[dict]]
    fetch_relationship_data: Callable[[str, str], Awaitable[dict]]
    fetch_recent_events: Callable[[str, str, int], Awaitable[list[dict]]]
    get_location_name_from_id: Callable[[str | None], Awaitable[str | None]]
    fetch_location: Callable[[str], Awaitable[str]]
    fetch_location_hierarchy: Callable[[str], Awaitable[list[dict]]]
    detect_present_npcs: Callable[[str, str, dict[str, str]], list[str]]
    fetch_npc_profiles: Callable[[list[str], str, str], Awaitable[list[dict]]]
