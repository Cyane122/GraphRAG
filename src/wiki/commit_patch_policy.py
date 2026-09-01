# ================================
# src/wiki/commit_patch_policy.py
#
# Dispatches per-document-type gameplay patch policy for the Wiki Updater: which section a
# patch may target, whose evidence it needs, and what each document type additionally forbids.
#
# Classes
#   - _PatchPolicyContext : Read-only per-patch values a document-type policy hook may need.
#   - _PatchPolicy : One document type's gameplay-patch policy record for the dispatch table.
#
# Functions
#   - validate_patch_policy(patch: SectionPatch, document: WikiDocument, *, user_input: str, actor_response: str, original_markdown: str, player_document: str | None, player_profile_id: str, actor_profile_id: str, player_reference_tokens: set[str], active_character_documents: set[str], scene_active_profile_ids: set[str], owner_reference_tokens: dict[str, set[str]]) -> None : Validate source authority and canonical scope for one section patch.
#
# The type -> policy dispatch table (_PATCH_POLICIES) and its named per-type hooks
# (_check_scene_patch, _check_relationship_patch, _check_owned_root_patch,
# _check_event_patch, _check_character_patch) replace a former sequential if-chain;
# see validate_patch_policy for the dispatch order.
# ================================

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from src.wiki.commit_errors import WikiCommitPlanningError
from src.wiki.evidence import actor_source_player_conflicts, evidence_is_exact_quote
from src.wiki.models import SectionPatch, WikiDocument, WikiDocumentType

_RELATIONSHIP_SECTION = "Relationship Development"
_RELATIONSHIP_EMPTY_SENTINEL = "- No durable relationship change has occurred since the story began."
_SECRET_STATUS_RE = re.compile(
    r"(?m)^-\s*상태:\s*(hidden|suspected|revealed)\s*$",
    re.IGNORECASE,
)
_OWNED_ROOT_SECTIONS = {
    "goal": "진행 상태",
    "item": "현재 상태",
    "secret": "공개 상태",
}
_CHARACTER_RUNTIME_OWNED_SECTIONS = {
    "욕구와 컨디션",
    "Personality Change Ledger",
    "Reproductive State",
}


def _relationship_bullets(markdown: str) -> set[str]:
    """Return normalized bullet lines in a relationship section."""
    return {
        line.strip()
        for line in markdown.splitlines()
        if line.lstrip().startswith(("- ", "* "))
    }


def _secret_disclosure_status(markdown: str) -> str | None:
    """Extract a Secret disclosure status from section Markdown."""
    match = _SECRET_STATUS_RE.search(markdown)
    return match.group(1).casefold() if match is not None else None


@dataclass(frozen=True)
class _PatchPolicyContext:
    """Read-only per-patch values a document-type policy hook may need."""

    section_path: tuple[str, ...]
    user_input: str
    actor_response: str
    original_markdown: str
    player_document: str | None
    player_profile_id: str
    actor_profile_id: str
    player_reference_tokens: set[str]
    active_character_documents: set[str]
    scene_active_profile_ids: set[str]
    owner_reference_tokens: dict[str, set[str]]


@dataclass(frozen=True)
class _PatchPolicy:
    """One document type's gameplay-patch policy record for the dispatch table."""

    reject_message: str | None = None
    reject_before_player_evidence_gate: bool = False
    allows_player_evidence: bool = False
    hook: Callable[[SectionPatch, WikiDocument, _PatchPolicyContext], None] | None = None


def _check_scene_patch(
    patch: SectionPatch,
    document: WikiDocument,
    ctx: _PatchPolicyContext,
) -> None:
    """Enforce complete current-scene H2 replacement and actor-source player conflicts."""
    if len(ctx.section_path) != 1 or ctx.section_path[0] not in {"현재 장면", "시작 기준"}:
        raise WikiCommitPlanningError(
            "scene updates must replace the complete current-scene H2 section"
        )
    if patch.evidence_source == "actor_response":
        player_conflicts = actor_source_player_conflicts(
            ctx.original_markdown,
            patch.replacement_markdown,
            ctx.player_reference_tokens,
        )
        if player_conflicts and patch.player_evidence is None:
            details = " | ".join(player_conflicts[:3])
            raise WikiCommitPlanningError(
                "Actor-sourced scene patch can add player or shared movement only "
                "when player_evidence is an exact quote from Player Input; "
                f"conflicting added lines: {details}"
            )


def _check_relationship_patch(
    patch: SectionPatch,
    document: WikiDocument,
    ctx: _PatchPolicyContext,
) -> None:
    """Enforce owner scope, evidence authority, and durable-bullet preservation.

    The current Actor's own relationship document is always in scope. A
    non-Actor owner is in scope only when that owner is a scene-active
    character per `scene_active_profile_ids` (name-matched against
    `scene/current.md`, the same judgment `commit_policy.scene_active_profile_ids`
    applies to third-party Memory/Goal/Item/Secret creation authority); its
    evidence must additionally name that owner via `owner_reference_tokens`.
    An owner that is neither the Actor nor scene-active is rejected outright —
    unlike a third-party creation, a relationship patch is never severable
    (see `commit_errors.SeverableCreationAuthorityError`); any violation here
    fails the whole Updater attempt. NPC-to-NPC relationship documents remain
    out of scope: lazy materialization only ever creates an owner-to-player
    ledger (`context.materialize_scene_active_relationships`).
    """
    metadata = document.metadata
    assert metadata is not None
    if ctx.section_path != (_RELATIONSHIP_SECTION,):
        raise WikiCommitPlanningError(
            "relationship updates must replace the complete "
            "Relationship Development H2 section"
        )
    if metadata.owner != ctx.actor_profile_id:
        if metadata.owner not in ctx.scene_active_profile_ids:
            raise WikiCommitPlanningError(
                "relationship updates are limited to the active Actor or a "
                "scene-active character's own relationship document"
            )
        owner_tokens = ctx.owner_reference_tokens.get(metadata.owner, set())
        if owner_tokens and not any(token in patch.evidence for token in owner_tokens):
            raise WikiCommitPlanningError(
                f"relationship evidence does not name owner {metadata.owner}: {patch.evidence!r}"
            )
    if patch.evidence_source != "actor_response":
        raise WikiCommitPlanningError(
            "relationship changes require Actor Response evidence"
        )
    player_conflicts = actor_source_player_conflicts(
        ctx.original_markdown,
        patch.replacement_markdown,
        ctx.player_reference_tokens,
    )
    if player_conflicts:
        details = " | ".join(player_conflicts[:3])
        raise WikiCommitPlanningError(
            "Actor-sourced relationship patch cannot establish player action "
            f"or internal state; conflicting added lines: {details}"
        )
    original_bullets = _relationship_bullets(ctx.original_markdown)
    replacement_bullets = _relationship_bullets(patch.replacement_markdown)
    durable_bullets = original_bullets - {_RELATIONSHIP_EMPTY_SENTINEL}
    missing_bullets = durable_bullets - replacement_bullets
    if missing_bullets:
        raise WikiCommitPlanningError(
            "relationship updates must preserve every accepted durable change"
        )
    added_bullets = replacement_bullets - original_bullets
    if (
        _RELATIONSHIP_EMPTY_SENTINEL in original_bullets
        and _RELATIONSHIP_EMPTY_SENTINEL not in replacement_bullets
        and not added_bullets
    ):
        raise WikiCommitPlanningError(
            "relationship empty sentinel can be removed only with a new durable change"
        )


def _check_owned_root_patch(
    patch: SectionPatch,
    document: WikiDocument,
    ctx: _PatchPolicyContext,
) -> None:
    """Enforce the single owned-root section for goal/item/secret and secret status lock."""
    metadata = document.metadata
    assert metadata is not None
    document_type = metadata.type
    allowed_root = _OWNED_ROOT_SECTIONS[document_type]
    if not ctx.section_path or ctx.section_path[0] != allowed_root:
        raise WikiCommitPlanningError(
            f"{document_type} updates may modify only the '{allowed_root}' "
            f"section: {patch.document}"
        )
    if metadata.owner == ctx.player_profile_id and patch.evidence_source != "player_input":
        raise WikiCommitPlanningError(
            f"Player-owned {document_type} state requires evidence from Player Input"
        )
    if document_type == "secret":
        original_status = _secret_disclosure_status(ctx.original_markdown)
        replacement_status = _secret_disclosure_status(patch.replacement_markdown)
        if original_status != replacement_status:
            raise WikiCommitPlanningError(
                "Runtime-owned secret disclosure status cannot be patched by the "
                f"gameplay model: {patch.document}"
            )


def _check_event_patch(
    patch: SectionPatch,
    document: WikiDocument,
    ctx: _PatchPolicyContext,
) -> None:
    """Enforce the '진행 상태' root section and forbid reopening a concluded event."""
    if not ctx.section_path or ctx.section_path[0] != "진행 상태":
        raise WikiCommitPlanningError(
            "event updates may modify only the '진행 상태' "
            f"section: {patch.document}"
        )
    original_status = None
    for line in ctx.original_markdown.splitlines():
        if line.startswith("- 상태:"):
            original_status = line.partition(":")[2].strip().casefold()
            break
    replacement_statuses: list[str] = []
    for line in patch.replacement_markdown.splitlines():
        if line.startswith("- 상태:"):
            replacement_statuses.append(line.partition(":")[2].strip().casefold())
    if replacement_statuses != ["ongoing"] and replacement_statuses != ["concluded"]:
        raise WikiCommitPlanningError(
            "event progress must include exactly one '- 상태:' line with "
            f"'ongoing' or 'concluded': {patch.document}"
        )
    replacement_status = replacement_statuses[0]
    if original_status == "concluded" and replacement_status == "ongoing":
        raise WikiCommitPlanningError(
            "Event progress cannot reopen a concluded record: "
            f"{patch.document}"
        )


def _check_character_patch(
    patch: SectionPatch,
    document: WikiDocument,
    ctx: _PatchPolicyContext,
) -> None:
    """Enforce the current-state H3 scope, runtime-owned sections, and player evidence."""
    if not ctx.section_path or ctx.section_path[0] != "현재 상태":
        raise WikiCommitPlanningError(
            f"Gameplay updater cannot modify static character sections: {patch.document}"
        )
    if len(ctx.section_path) != 2:
        raise WikiCommitPlanningError(
            "Character gameplay updates must target one complete current-state H3"
        )
    if ctx.section_path[1] in _CHARACTER_RUNTIME_OWNED_SECTIONS:
        raise WikiCommitPlanningError(
            f"Runtime-owned character section cannot be patched by the gameplay model: "
            f"{ctx.section_path[1]}"
        )
    if patch.document == ctx.player_document and patch.evidence_source != "player_input":
        raise WikiCommitPlanningError(
            "Player character state requires evidence from Player Input"
        )
    if (
        patch.document in ctx.active_character_documents
        and ctx.section_path == ("현재 상태", "현재 위치와 활동")
    ):
        raise WikiCommitPlanningError(
            "Active character location/activity belongs in scene/current.md"
        )


_PATCH_POLICIES: dict[WikiDocumentType, _PatchPolicy] = {
    "thread": _PatchPolicy(
        reject_message="Updater cannot modify thread management documents",
        reject_before_player_evidence_gate=True,
    ),
    "scene": _PatchPolicy(allows_player_evidence=True, hook=_check_scene_patch),
    "relationship": _PatchPolicy(hook=_check_relationship_patch),
    "goal": _PatchPolicy(hook=_check_owned_root_patch),
    "item": _PatchPolicy(hook=_check_owned_root_patch),
    "secret": _PatchPolicy(hook=_check_owned_root_patch),
    "event": _PatchPolicy(hook=_check_event_patch),
    "memory": _PatchPolicy(
        reject_message=(
            "Gameplay updater cannot patch memory documents; gated memory distortion "
            "owns their mutable section: {document}"
        )
    ),
    "character": _PatchPolicy(hook=_check_character_patch),
    # No gameplay patch policy exists for these 7 types today; a patch of these types
    # is accepted once the common evidence/player_evidence checks pass. This is a
    # pre-existing gap (see .re0/iteration/0.1.1-engine-dedup/EVIDENCE.local.md G3),
    # not new behavior introduced by this table — do not add checks here as part of
    # this change.
    "character_profile": _PatchPolicy(),
    "location": _PatchPolicy(),
    "organization": _PatchPolicy(),
    "prose": _PatchPolicy(),
    "scenario": _PatchPolicy(),
    "scene_prompt": _PatchPolicy(),
    "world": _PatchPolicy(),
}


def validate_patch_policy(
    patch: SectionPatch,
    document: WikiDocument,
    *,
    user_input: str,
    actor_response: str,
    original_markdown: str,
    player_document: str | None,
    player_profile_id: str,
    actor_profile_id: str,
    player_reference_tokens: set[str],
    active_character_documents: set[str],
    scene_active_profile_ids: set[str],
    owner_reference_tokens: dict[str, set[str]],
) -> None:
    """Validate source authority and canonical scope for one section patch."""
    source_text = user_input if patch.evidence_source == "player_input" else actor_response
    if not evidence_is_exact_quote(patch.evidence, source_text):
        raise WikiCommitPlanningError(
            f"Updater evidence is not an exact quote from {patch.evidence_source}: "
            f"{patch.document}"
        )
    if patch.player_evidence is not None and not evidence_is_exact_quote(
        patch.player_evidence,
        user_input,
    ):
        raise WikiCommitPlanningError(
            "Updater player_evidence is not an exact quote from player_input: "
            f"{patch.document}"
        )

    metadata = document.metadata
    if metadata is None:
        raise WikiCommitPlanningError(f"Updater targeted a document without metadata: {patch.document}")

    policy = _PATCH_POLICIES.get(metadata.type, _PatchPolicy())
    if policy.reject_message is not None and policy.reject_before_player_evidence_gate:
        raise WikiCommitPlanningError(policy.reject_message.format(document=patch.document))
    if patch.player_evidence is not None and not policy.allows_player_evidence:
        raise WikiCommitPlanningError(
            "player_evidence is supported only for complete current-scene patches"
        )
    if policy.reject_message is not None:
        raise WikiCommitPlanningError(policy.reject_message.format(document=patch.document))
    if policy.hook is None:
        return
    ctx = _PatchPolicyContext(
        section_path=tuple(patch.section_path),
        user_input=user_input,
        actor_response=actor_response,
        original_markdown=original_markdown,
        player_document=player_document,
        player_profile_id=player_profile_id,
        actor_profile_id=actor_profile_id,
        player_reference_tokens=player_reference_tokens,
        active_character_documents=active_character_documents,
        scene_active_profile_ids=scene_active_profile_ids,
        owner_reference_tokens=owner_reference_tokens,
    )
    policy.hook(patch, document, ctx)
