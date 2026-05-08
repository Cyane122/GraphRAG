# ================================
# src/simulation/systems/__init__.py
#
# simulation.systems package public interface.
#
# Functions
#   - fetch_goal_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int = 2) -> list[dict] : Fetch active goal hints.
#   - apply_goal_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply goal progress updates.
#   - fetch_secret_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int = 2) -> list[dict] : Fetch eligible secret subtext hints.
#   - apply_secret_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply secret reveal updates.
#   - fetch_object_memory_hints(owner_id: str, pc_id: str, location_id: str, user_input: str, limit: int = 2) -> list[dict] : Fetch relevant item-memory hints.
#   - apply_item_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply practical item updates.
# ================================

from src.simulation.systems.memory import ensure_memories_for_event, run_decay, distort_on_affinity_change
from src.simulation.systems.needs import ensure_traits, run_needs_update
from src.simulation.systems.organic import tick_cycle_day, process_ejaculation
from src.simulation.systems.social import build_world_context, resolve_and_update
from src.simulation.systems.reputation import propagate_gossip
from src.simulation.systems.personality import check_personality_drift
from src.simulation.systems.goals import fetch_goal_hints, apply_goal_updates
from src.simulation.systems.secrets import fetch_secret_hints, apply_secret_updates
from src.simulation.systems.items import fetch_object_memory_hints, apply_item_updates

__all__ = [
    "ensure_memories_for_event", "run_decay", "distort_on_affinity_change",
    "ensure_traits", "run_needs_update",
    "tick_cycle_day", "process_ejaculation",
    "build_world_context", "resolve_and_update",
    "propagate_gossip",
    "check_personality_drift",
    "fetch_goal_hints", "apply_goal_updates",
    "fetch_secret_hints", "apply_secret_updates",
    "fetch_object_memory_hints", "apply_item_updates",
]
