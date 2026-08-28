# ================================
# src/agents/manager/models.py
#
# Shared data bundles for the Manager pipeline.
#
# Classes
#   - ManagerBootstrap : World instance, config, and global state bundle
#   - SceneTimePlan : Scene types and prompt time baseline bundle
#   - CoreContext : Graph context bundle for prompt rendering
#   - PromptParts : Fixed, genre, and dynamic prompt bundle
# ================================
from dataclasses import dataclass
from datetime import datetime

from src.assets.worlds.base import World

@dataclass
class ManagerBootstrap:
    """World config and base state loaded before turn planning."""

    world: World
    world_config: dict
    global_state: dict


@dataclass
class SceneTimePlan:
    """Scene classification plus the prompt time baseline."""

    parse_result: dict
    scene_types: list[str]
    base_time: datetime
    current_dt: datetime
    time_plan: dict
    manager_effects: dict
    schedule_context: dict


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
    active_npcs: list[dict]
    ambient_npcs: list[dict]
    scene_state: dict
    context_plan: dict
    pc_in_scene: bool = False


@dataclass
class PromptParts:
    """Final prompt segments returned by the manager."""

    fixed: str
    genre: str
    dynamic: str
