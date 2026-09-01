# ================================
# src/wiki/commit_errors.py
#
# Defines the shared exceptions for Wiki commit planning phases.
#
# Classes
#   - WikiCommitPlanningError : Raised when a Wiki commit proposal is invalid or retries are exhausted.
#   - SeverableCreationAuthorityError : Raised for one independent creation's owner-authority violation that the validator may sever instead of failing the whole Updater attempt.
# ================================

from __future__ import annotations


class WikiCommitPlanningError(RuntimeError):
    """Raised when the Wiki commit planner cannot produce a valid proposal."""


class SeverableCreationAuthorityError(WikiCommitPlanningError):
    """Raised when one independent creation's owner is an active thread profile
    but is not present in the current scene.

    Owner authority extends to the player, the active Actor, and any other
    active thread character named in `scene/current.md`
    (`commit_policy.scene_active_profile_ids`). A real thread profile that
    simply is not part of the current scene falls through to this branch.

    Unlike every other `WikiCommitPlanningError` cause, this one is narrow
    enough that the caller may drop just the offending creation ("sever" it)
    and keep validating the rest of the Updater result, instead of rejecting
    the whole attempt and forcing a retry. It is raised from exactly one
    branch each in `_validate_owner_creation_authority` and
    `_validate_memory_creation_authority` (via the shared
    `_validate_third_party_owner_authority` helper) in
    `src/wiki/commit_policy.py` — every other branch in those functions
    (unknown owner, missing related Event, wrong evidence source, or
    evidence that never names a scene-active third-party owner) still raises
    the plain, fatal `WikiCommitPlanningError`, because those indicate the
    model picked the wrong evidence rather than an absent participant.
    """
