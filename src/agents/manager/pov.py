# ================================
# src/agents/manager/pov.py
#
# Current first-person POV selection helpers for manager prompt rendering.
#
# Functions
#   - build_current_pov_context(context: CoreContext, scene_types: list[str], user_input: str, recent_story: str) -> dict : Build prompt-ready current POV metadata
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


def build_current_pov_context(
    context: CoreContext,
    scene_types: list[str],
    user_input: str,
    recent_story: str,
) -> dict:
    """Build compact current-POV metadata from already fetched character records."""
    candidates = [_candidate_from_primary(context)]
    candidates.extend(_candidate_from_present_npc(npc) for npc in context.npcs)
    candidates = [candidate for candidate in candidates if candidate.get("id")]

    selected = _select_candidate(candidates, scene_types, user_input, recent_story)
    return {
        "selected": selected,
        "candidates": candidates,
        "rule": (
            "All first-person I/me/my narration belongs to CURRENT_POV only. "
            "If contact focus shifts to another listed candidate, switch CURRENT_POV before drafting. "
            "Never invent placeholder POV labels like 직장인 A or 여학생 B; use an existing candidate or a full Korean name."
        ),
    }


def _candidate_from_primary(context: CoreContext) -> dict:
    """Create a POV candidate from the main character node."""
    profile = context.char_data.get("static_profile") or {}
    return {
        "id": context.char_data.get("id"),
        "name": context.char_data.get("name"),
        "aliases": context.char_data.get("aliases") or [],
        "source": "primary_character",
        "profile": _compact_profile(profile),
        "dynamic_state": _compact_dynamic_state(context.char_data.get("dynamic_state") or {}),
        "relationship_to_pc": _compact_relationship(context.relationship),
    }


def _candidate_from_present_npc(npc: dict) -> dict:
    """Create a POV candidate from a detected present NPC profile."""
    return {
        "id": npc.get("char_id"),
        "name": npc.get("name"),
        "aliases": npc.get("aliases") or [],
        "source": "present_npc",
        "profile": _compact_profile(npc.get("profile") or {}),
        "dynamic_state": _compact_dynamic_state(npc.get("dynamic_state") or {}),
        "relationship_to_pc": _compact_relationship(npc.get("rel_to_pc") or {}),
    }


def _select_candidate(
    candidates: list[dict],
    scene_types: list[str],
    user_input: str,
    recent_story: str,
) -> dict:
    """Select the most likely POV owner using current-turn mentions first."""
    if not candidates:
        return {}

    mentioned = _latest_mentioned_candidate(candidates, user_input)
    if mentioned:
        return _with_reason(mentioned, "named in current user input")

    if any(scene in scene_types for scene in ("physical", "intimate")):
        present_npcs = [candidate for candidate in candidates if candidate.get("source") == "present_npc"]
        if len(present_npcs) == 1:
            return _with_reason(present_npcs[0], "single present NPC in physical/intimate scene")

    mentioned = _latest_mentioned_candidate(candidates, recent_story[-1200:])
    if mentioned:
        return _with_reason(mentioned, "latest named candidate in recent story")

    return _with_reason(candidates[0], "fallback to primary character")


def _latest_mentioned_candidate(candidates: list[dict], text: str) -> dict | None:
    """Return the candidate whose name appears latest in text."""
    best: tuple[int, dict] | None = None
    for candidate in candidates:
        names = [candidate.get("name"), candidate.get("id"), *(candidate.get("aliases") or [])]
        latest = max((text.rfind(name) for name in names if name), default=-1)
        if latest >= 0 and (best is None or latest > best[0]):
            best = (latest, candidate)
    return best[1] if best else None


def _with_reason(candidate: dict, reason: str) -> dict:
    """Copy a selected candidate with a model-facing selection reason."""
    selected = dict(candidate)
    selected["selection_reason"] = reason
    return selected


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
