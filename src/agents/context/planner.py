# ================================
# src/agents/context/planner.py
#
# Rule-based context planning for Manager dynamic prompt preparation.
#
# Classes
#   - ContextPlan : selected systems, nodes, query focus, policies, and character budgets
#
# Functions
#   - build_context_plan(scene_types: list[str], user_input: str, scene_state: dict, world_config: dict | None) -> ContextPlan : choose dynamic context scope for the turn
#   - context_plan_to_prompt_dict(plan: ContextPlan) -> dict : prompt-safe plan dict
# ================================

from dataclasses import asdict, dataclass, field
import re

from src.agents.context.scene_keys import normalize_scene_type


_MEMORY_RE = re.compile(r"(remember|memory|again|last time|기억|지난|전에|또|다시)")
_SECRET_RE = re.compile(r"(secret|hide|truth|비밀|숨기|진실|말 못)")
_SOCIAL_RE = re.compile(r"(sns|소문|평판|친구|동료|주변|rumor|reputation)")
_ITEM_RE = re.compile(r"(목걸이|반지|선물|사진|편지|물건|item|gift|letter|photo)")
_GOAL_RE = re.compile(r"(목표|일정|해야|약속|준비|goal|promise|schedule)")


@dataclass
class ContextPlan:
    """Rule-based context scope for the current turn."""

    scene_type: str
    scene_modifiers: list[str]
    importance: int
    required_systems: list[str]
    required_nodes: list[str]
    skip_systems: list[str]
    query_focus: list[str]
    priority_order: list[str]
    freshness_policy: dict[str, str]
    conflict_resolution_policy: dict[str, str]
    budget: dict[str, int] = field(default_factory=dict)


def build_context_plan(
    scene_types: list[str],
    user_input: str,
    scene_state: dict,
    world_config: dict | None = None,
) -> ContextPlan:
    """Choose the dynamic context scope for this turn using conservative rules."""
    normalized_scene_types = _normalize_scene_types(scene_types)
    scene_type = normalized_scene_types[0]
    scene_modifiers = normalized_scene_types[1:]
    text = f"{user_input}\n{scene_state.get('last_action', '')}\n{' '.join(scene_state.get('unresolved_beats', []))}"
    required_systems = ["scene_state", "location", "relationship"]
    required_nodes = ["Location", "Rule", "SpeechProfile", "RelationshipProfile"]
    query_focus = ["current_scene", "location", "rules", "speech", "relationship"]

    importance = _base_importance(scene_type, float(scene_state.get("tension") or 0.0))
    budget = _default_budget(world_config)

    if _should_include_memory(text, scene_type, importance):
        required_systems.append("memory")
        required_nodes.append("Memory")
        query_focus.append("recent_memory")

    if _SECRET_RE.search(text):
        required_systems.append("secrets")
        required_nodes.append("Secret")
        query_focus.append("subtext")

    if _SOCIAL_RE.search(text) or scene_type == "workplace":
        required_systems.append("social")
        query_focus.append("nearby_activity")

    if _ITEM_RE.search(text):
        required_systems.append("items")
        required_nodes.append("Item")
        query_focus.append("object_memory")

    if _GOAL_RE.search(text) or importance >= 6:
        required_systems.append("goals")
        required_nodes.append("Goal")
        query_focus.append("long_term_pressure")

    required_systems = _dedupe(required_systems)
    required_nodes = _dedupe(required_nodes)
    query_focus = _dedupe(query_focus)
    all_optional = {"memory", "secrets", "social", "items", "goals"}
    skip_systems = sorted(all_optional - set(required_systems))
    priority_order = _build_priority_order(required_systems, scene_type, scene_modifiers)

    return ContextPlan(
        scene_type=scene_type,
        scene_modifiers=scene_modifiers,
        importance=importance,
        required_systems=required_systems,
        required_nodes=required_nodes,
        skip_systems=skip_systems,
        query_focus=query_focus,
        priority_order=priority_order,
        freshness_policy=_freshness_policy(),
        conflict_resolution_policy=_conflict_resolution_policy(),
        budget=budget,
    )


def context_plan_to_prompt_dict(plan: ContextPlan) -> dict:
    """Return a prompt-safe dict representation of ContextPlan."""
    return asdict(plan)


def _normalize_scene_types(scene_types: list[str]) -> list[str]:
    """Normalize the primary scene and preserve modifier labels for policy checks."""
    result: list[str] = []
    for scene_type in scene_types or ["daily"]:
        raw_key = str(scene_type or "daily").strip().lower() or "daily"
        key = normalize_scene_type(raw_key) if not result else raw_key
        if key not in result:
            result.append(key)
    return result or ["daily"]


def _build_priority_order(
    required_systems: list[str],
    scene_type: str,
    scene_modifiers: list[str],
) -> list[str]:
    """Build retrieval priority with scene pressure before optional context."""
    base = ["scene_state", "location", "relationship", "rules", "speech"]
    if scene_type in {"emotional", "vulnerable", "intimate"} or "vulnerable" in scene_modifiers:
        optional = ["memory", "goals", "secrets", "items", "social"]
    elif scene_type in {"physical", "tense"} or "tense" in scene_modifiers:
        optional = ["goals", "items", "memory", "secrets", "social"]
    else:
        optional = ["memory", "social", "items", "goals", "secrets"]
    return _dedupe([*base, *(system for system in optional if system in required_systems)])


def _freshness_policy() -> dict[str, str]:
    """Return default freshness rules for context retrieval."""
    return {
        "scene_state": "current_turn",
        "location": "current_turn",
        "relationship": "latest_profile_then_current_state",
        "memory": "pinned_then_recent_then_relevant",
        "goals": "active_first",
        "secrets": "cooldown_and_condition_gated",
        "schedules": "current_day_window_first",
    }


def _conflict_resolution_policy() -> dict[str, str]:
    """Return default policy for conflicting graph context."""
    return {
        "user_input": "highest_priority_current_turn",
        "dynamic_state": "prefer_latest_committed_state",
        "schedule_vs_location": "schedule_is_pressure_not_teleportation",
        "relationship_profile_vs_relationship_edge": "profile_guides_tone_edge_guides_scores",
        "memory_vs_event": "event_preserves_facts_memory_preserves_subjective_view",
    }


def _base_importance(scene_type: str, tension: float) -> int:
    """Infer broad turn importance from scene type and tension."""
    base = {
        "daily": 3,
        "bonding": 5,
        "aegyo": 3,
        "emotional": 5,
        "vulnerable": 6,
        "physical": 5,
        "tense": 5,
        "atmospheric": 3,
        "workplace": 4,
        "intimate": 6,
    }.get(scene_type, 3)
    if tension >= 0.7:
        base += 2
    elif tension >= 0.45:
        base += 1
    return max(1, min(10, base))


def _should_include_memory(text: str, scene_type: str, importance: int) -> bool:
    """Decide whether memory context is worth querying/rendering."""
    if _MEMORY_RE.search(text):
        return True
    if scene_type in {"bonding", "emotional", "vulnerable", "intimate"}:
        return True
    return importance >= 6


def _default_budget(world_config: dict | None) -> dict[str, int]:
    """Return dynamic context budgets, allowing world-level overrides."""
    default = {
        "scene": 260,
        "characters": 500,
        "location": 180,
        "memories": 500,
        "relationships": 320,
        "goals": 220,
        "items": 180,
        "subtext": 220,
        "schedules": 250,
        "recent_summary": 500,
    }
    override = (world_config or {}).get("context_budget", {})
    if isinstance(override, dict):
        default.update({k: int(v) for k, v in override.items() if isinstance(v, (int, float))})
    return default


def _dedupe(items: list[str]) -> list[str]:
    """Deduplicate while preserving order."""
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
