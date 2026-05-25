# ================================
# src/simulation/systems/__init__.py
#
# Long-term simulation system facade.
#
# Functions
#   - ensure_memories_for_event(event_id: str, summary: str, importance: int, char_ids: list[str], timestamp: str, embedding: list[float] | None = None, memory_type: str = "episodic", actor_response: str = "") -> None : Create character memories for an event.
#   - run_decay(current_game_time: datetime) -> None : Apply memory decay, compression, distortion, and deletion.
#   - distort_on_affinity_change(char_id: str, pc_id: str, affinity_delta, current_game_time) -> None : Reinterpret memories after large affinity changes.
#   - ensure_traits(char_id: str) -> dict : Generate and save missing trait_* fields from StaticProfile.
#   - ensure_traits_for_characters(characters: list[dict]) -> dict : Initialize traits from a character list.
#   - run_needs_update(pc_id: str, elapsed_minutes: float, current_time: datetime, scene_chars: list[str] | None = None, schedule_rows: list[dict] | None = None, allow_location_moves: bool = True) -> dict : Update all NPC need states.
#   - tick_cycle_day(char_id: str, days: int) -> None : Advance one character's organic cycle.
#   - tick_all_cycles(days: int) -> None : Advance organic cycles for all relevant characters.
#   - process_ejaculation(actor_response: str, pc_id: str, npc_id: str, current_time: datetime) -> dict : Apply organic state changes from actor response.
#   - build_world_context(npc_id: str, pc_id: str, location_id: str, current_time: datetime, enable_sns: bool = True) -> dict : Build nearby activity and optional SNS feed context.
#   - resolve_and_update(char_names: list[str], main_npc_id: str, pc_id: str, world_config: dict, event_id: str | None = None, event_importance: int = 0) -> list[str] : Resolve characters and update appearance records.
#   - propagate_gossip(event_summary: str, event_importance: int, relationship_delta: int, source_npc_id: str, pc_id: str, timestamp_iso: str, source_event_id: str | None = None) -> None : Propagate reputation changes.
#   - check_personality_drift(char_id: str, event_id: str, event_summary: str, importance: int, current_time: datetime) -> None : Apply personality drift when events qualify.
#   - fetch_goal_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int = 2) -> list[dict] : Fetch active goal hints.
#   - apply_goal_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply goal progress updates.
#   - fetch_secret_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int = 2) -> list[dict] : Fetch eligible secret subtext hints.
#   - apply_secret_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply secret reveal updates.
#   - fetch_object_memory_hints(owner_id: str, pc_id: str, location_id: str, user_input: str, limit: int = 2) -> list[dict] : Fetch relevant item-memory hints.
#   - apply_item_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Apply practical item updates.
#   - ensure_default_rooms(pc_id: str, npc_id: str, current_time: datetime) -> None : Ensure baseline KakaoTalk rooms exist.
#   - generate_turn_messages(pc_id: str, npc_id: str, current_time: datetime, recent_story: str = "") -> list[str] : Generate autonomous KakaoTalk messages.
#   - process_kakao_before_actor(pc_id: str, npc_id: str, current_time: datetime, pending_player_messages: list[dict], recent_story: str = "", world_hints: dict | None = None) -> dict : Build deferred KakaoTalk turn context and effects.
#   - commit_kakao_effects(effects: list[dict]) -> None : Persist accepted deferred KakaoTalk message effects.
#   - fetch_kakao_panel_state(pc_id: str, active_room_id: str | None = None) -> dict : Fetch UI-ready KakaoTalk panel state.
#   - fetch_kakao_context(pc_id: str, limit_rooms: int = 3, limit_messages: int = 5) -> list[dict] : Fetch prompt-ready KakaoTalk room context.
# ================================

from src.simulation.systems.memory import ensure_memories_for_event, run_decay, distort_on_affinity_change
from src.simulation.systems.needs import ensure_traits, ensure_traits_for_characters, run_needs_update
from src.simulation.systems.organic import tick_cycle_day, tick_all_cycles, process_ejaculation
from src.simulation.systems.social import build_world_context, resolve_and_update
from src.simulation.systems.reputation import propagate_gossip
from src.simulation.systems.personality import check_personality_drift
from src.simulation.systems.goals import fetch_goal_hints, apply_goal_updates
from src.simulation.systems.secrets import fetch_secret_hints, apply_secret_updates
from src.simulation.systems.items import fetch_object_memory_hints, apply_item_updates
from src.simulation.systems.kakao import (
    ensure_default_rooms,
    fetch_kakao_context,
    fetch_kakao_panel_state,
    generate_turn_messages,
    process_kakao_before_actor,
    commit_kakao_effects,
)

__all__ = [
    "ensure_memories_for_event", "run_decay", "distort_on_affinity_change",
    "ensure_traits", "ensure_traits_for_characters", "run_needs_update",
    "tick_cycle_day", "tick_all_cycles", "process_ejaculation",
    "build_world_context", "resolve_and_update",
    "propagate_gossip",
    "check_personality_drift",
    "fetch_goal_hints", "apply_goal_updates",
    "fetch_secret_hints", "apply_secret_updates",
    "fetch_object_memory_hints", "apply_item_updates",
    "ensure_default_rooms", "fetch_kakao_context", "fetch_kakao_panel_state", "generate_turn_messages",
    "process_kakao_before_actor", "commit_kakao_effects",
]
