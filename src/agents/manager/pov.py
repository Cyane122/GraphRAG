# ================================
# src/agents/manager/pov.py
#
# Fixed POV anchor helpers for manager prompt rendering.
#
# Functions
#   - build_current_pov_context(context: CoreContext, world_config: dict) -> dict : Build prompt-ready fixed POV metadata
# ================================

from src.agents.manager.models import CoreContext


_PROFILE_KEYS = (
    "age",
    "gender",
    "role",
    "appearance",
    "personality",
    "partner_status",
    "sexual_experience",
    "measurements",
    "speech_style",
    "distinctive_traits",
    "current_status",
)


def build_current_pov_context(context: CoreContext, world_config: dict) -> dict:
    """Build compact metadata for the fixed POV anchor."""
    pov_mode = _resolve_pov_mode(world_config)
    selected = (
        _anchor_from_user(context)
        if pov_mode.endswith("_user")
        else _anchor_from_primary(context)
    )
    return {
        "selected": selected,
        "rule": (
            "All perception, body response, immediate thought, and judgment belong to CURRENT_POV only. "
            "Do not switch POV during analysis or drafting. "
            "Other characters' hidden thoughts, feelings, intentions, and sensations must remain inaccessible."
        ),
    }


def _resolve_pov_mode(world_config: dict) -> str:
    """Return the configured POV mode, falling back to char-anchored third person."""
    return str(
        world_config.get("pov_mode")
        or world_config.get("pov_type")
        or world_config.get("prompt", {}).get("pov", {}).get("mode")
        or "3p_char"
    )


def _anchor_from_primary(context: CoreContext) -> dict:
    """Create fixed POV metadata from the main character node."""
    profile = context.char_data.get("static_profile") or {}
    return {
        "id": context.char_data.get("id"),
        "name": context.char_data.get("name"),
        "aliases": context.char_data.get("aliases") or [],
        "source": "primary_character_anchor",
        "profile": _compact_profile(profile),
        "dynamic_state": _compact_dynamic_state(context.char_data.get("dynamic_state") or {}),
        "relationship_to_pc": _compact_relationship(context.relationship),
    }


def _anchor_from_user(context: CoreContext) -> dict:
    """Create fixed POV metadata from the user/player character node."""
    profile = context.user_data.get("static_profile") or {}
    return {
        "id": context.user_data.get("id"),
        "name": context.user_data.get("name"),
        "aliases": context.user_data.get("aliases") or [],
        "source": "user_character_anchor",
        "profile": _compact_profile(profile),
        "dynamic_state": _compact_dynamic_state(context.user_data.get("dynamic_state") or {}),
        "relationship_to_pc": {},
    }


def _compact_profile(profile: dict) -> dict:
    """Keep only profile fields that steer first-person voice and self-perception."""
    return {key: profile[key] for key in _PROFILE_KEYS if profile.get(key)}


def _compact_dynamic_state(dynamic_state: dict) -> dict:
    """Keep dynamic fields relevant to POV continuity."""
    keys = (
        "mood",
        "mental_condition",
        "physical_condition",
        "stress_level",
        "location_id",
        "has_menstrual_cycle",
        "cycle_day",
        "pregnant",
        "pregnancy_day",
    )
    return {key: dynamic_state[key] for key in keys if dynamic_state.get(key) not in (None, "")}


def _compact_relationship(relationship: dict) -> dict:
    """Keep relationship fields that affect tone toward the player character."""
    keys = ("type", "status", "affinity", "trust", "familiarity", "summary")
    return {key: relationship[key] for key in keys if relationship.get(key) not in (None, "")}
