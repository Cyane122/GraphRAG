# ================================
# src/wiki/commit_policy.py
#
# Validates Wiki Updater results end to end: dispatches per-section patch policy via
# src.wiki.commit_patch_policy, then validates and renders new document creations.
#
# Functions
#   - validate_updater_result(result: WikiUpdaterResult, documents: list[WikiDocument], *, user_input: str, actor_response: str, player_document: str | None, player_profile_id: str, actor_profile_id: str, player_reference_tokens: set[str], active_character_documents: set[str], scene_active_profile_ids: set[str], commit_id: str, created_at: datetime, user_message_id: str | None = None, assistant_message_id: str | None = None) -> tuple[list[DocumentCreation], list[SeveredCreation]] : Validate an Updater result, record target section revisions, and return the sole-rendered creation documents plus any severed creations.
#   - active_thread_id(documents: list[WikiDocument]) -> str : Return the sole thread ID represented by Updater documents.
#   - event_titles_by_id(documents: list[WikiDocument], creations: list[CreateDocument]) -> dict[str, str] : Map existing and proposed Event IDs to visible titles.
#
# `document_body`, `player_reference_tokens`, and `scene_active_profile_ids` live in
# `src/wiki/evidence.py`, not here or in `context.py` — `context.py` needs the same
# scene-membership judgment for lazy relationship materialization, and importing it from
# `commit_policy.py` (which already depended on `context.document_body`) would cycle back.
# ================================

from __future__ import annotations

from datetime import datetime
import re

from src.wiki.commit_errors import SeverableCreationAuthorityError, WikiCommitPlanningError
from src.wiki.commit_patch_policy import validate_patch_policy
from src.wiki.document_creation import prepare_created_document
from src.wiki.evidence import (
    actor_source_player_conflicts,
    document_body,
    evidence_is_exact_quote,
    player_reference_tokens,
    player_reference_tokens as character_name_tokens,
    scene_active_profile_ids,
)
from src.wiki.markdown import apply_section_patches, document_revision, parse_markdown_sections
from src.wiki.models import (
    CreateDocument,
    CreateEventDocument,
    CreateGoalDocument,
    CreateItemDocument,
    CreateMemoryDocument,
    CreateSecretDocument,
    DocumentCreation,
    SectionPatch,
    SeveredCreation,
    WikiDocument,
    WikiUpdaterResult,
)
from src.wiki.prompt_contract import WikiPromptContractError, validate_actor_document_body

_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
SCENE_SECTION_ALIASES = ("현재 장면", "시작 기준")


def _normalize_scene_section_alias(
    patch: SectionPatch,
    document: WikiDocument,
    section_paths: set[tuple[str, ...]],
) -> None:
    """Normalize a legacy scene H2 alias only when one canonical H2 exists."""
    metadata = document.metadata
    if (
        metadata is None
        or metadata.type != "scene"
        or len(patch.section_path) != 1
        or patch.section_path[0] not in SCENE_SECTION_ALIASES
        or tuple(patch.section_path) in section_paths
    ):
        return
    existing_aliases = [
        alias for alias in SCENE_SECTION_ALIASES if (alias,) in section_paths
    ]
    if len(existing_aliases) != 1:
        return
    actual_title = existing_aliases[0]
    replacement_heading = re.compile(
        rf"\A##\s+(?:{'|'.join(re.escape(alias) for alias in SCENE_SECTION_ALIASES)})\s*$",
        re.MULTILINE,
    )
    if replacement_heading.match(patch.replacement_markdown) is None:
        return
    patch.section_path = (actual_title,)
    patch.replacement_markdown = replacement_heading.sub(
        f"## {actual_title}",
        patch.replacement_markdown,
        count=1,
    )


def _validate_actor_body_contract(
    body: str,
    document: WikiDocument,
    *,
    operation: str,
) -> None:
    """Translate Actor-body contract failures into planning errors."""
    try:
        validate_actor_document_body(body, document)
    except WikiPromptContractError as exc:
        raise WikiCommitPlanningError(
            f"{operation} violates Actor body contract for {document.path}: {exc}"
        ) from exc


def validate_updater_result(
    result: WikiUpdaterResult,
    documents: list[WikiDocument],
    *,
    user_input: str,
    actor_response: str,
    player_document: str | None,
    player_profile_id: str,
    actor_profile_id: str,
    player_reference_tokens: set[str],
    active_character_documents: set[str],
    scene_active_profile_ids: set[str],
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> tuple[list[DocumentCreation], list[SeveredCreation]]:
    """Validate patches and creations, record target section base revisions, and
    return the sole-rendered canonical documents for the validated creations
    alongside any independent creations severed for an owner-authority
    violation (see `_validate_document_creations`)."""
    by_path = {document.path: document for document in documents}
    if len(by_path) != len(documents):
        raise WikiCommitPlanningError("Updater input contains duplicate document paths")
    sections_by_path = {
        document.path: parse_markdown_sections(document.content)
        for document in documents
    }
    # Name tokens per active thread character profile ID, so a relationship patch whose
    # owner is a scene-active NPC (not the current Actor) can be checked for evidence that
    # actually names that owner — the same gate `_validate_third_party_owner_authority`
    # applies to third-party Memory/Goal/Item/Secret creation.
    # `character_name_tokens` is `evidence.player_reference_tokens` under a second name:
    # this function's own `player_reference_tokens` parameter (the player's tokens, for
    # the actor-source player-conflict checks) shadows the module-level import by name.
    owner_reference_tokens = {
        document.metadata.profile_id: character_name_tokens(document)
        for document in documents
        if document.metadata is not None
        and document.metadata.type == "character"
        and document.metadata.profile_id is not None
    }
    grouped: dict[str, list[SectionPatch]] = {}
    seen_targets: set[tuple[str, tuple[str, ...]]] = set()
    for patch in result.patches:
        if patch.document not in by_path:
            raise WikiCommitPlanningError(f"Updater targeted an unavailable document: {patch.document}")
        if patch.base_revision != by_path[patch.document].revision:
            raise WikiCommitPlanningError(f"Updater returned a stale revision: {patch.document}")
        if not patch.evidence.strip():
            raise WikiCommitPlanningError(f"Updater patch has no evidence: {patch.document}")
        if patch.confidence < 0.55:
            raise WikiCommitPlanningError(f"Updater patch confidence is too low: {patch.document}")
        _normalize_scene_section_alias(
            patch,
            by_path[patch.document],
            set(sections_by_path[patch.document]),
        )
        target = (patch.document, tuple(patch.section_path))
        if target in seen_targets:
            raise WikiCommitPlanningError(f"Updater returned a duplicate section patch: {target}")
        seen_targets.add(target)
        section = sections_by_path[patch.document].get(tuple(patch.section_path))
        if section is None:
            raise WikiCommitPlanningError(f"Updater targeted an unknown section: {target}")
        validate_patch_policy(
            patch,
            by_path[patch.document],
            user_input=user_input,
            actor_response=actor_response,
            original_markdown=section.markdown,
            player_document=player_document,
            player_profile_id=player_profile_id,
            actor_profile_id=actor_profile_id,
            player_reference_tokens=player_reference_tokens,
            active_character_documents=active_character_documents,
            scene_active_profile_ids=scene_active_profile_ids,
            owner_reference_tokens=owner_reference_tokens,
        )
        _validate_actor_body_contract(
            patch.replacement_markdown,
            by_path[patch.document],
            operation="Updater patch",
        )
        patch.base_section_revision = document_revision(section.markdown)
        patch.base_markdown = section.markdown
        grouped.setdefault(patch.document, []).append(patch)

    for path, patches in grouped.items():
        apply_section_patches(by_path[path], patches)
    return _validate_document_creations(
        result.creations,
        documents,
        user_input=user_input,
        actor_response=actor_response,
        player_reference_tokens=player_reference_tokens,
        player_profile_id=player_profile_id,
        actor_profile_id=actor_profile_id,
        scene_active_profile_ids=scene_active_profile_ids,
        thread_id=active_thread_id(documents),
        commit_id=commit_id,
        created_at=created_at,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
    )


def _validate_document_creations(
    creations: list[CreateDocument],
    documents: list[WikiDocument],
    *,
    user_input: str,
    actor_response: str,
    player_reference_tokens: set[str],
    player_profile_id: str,
    actor_profile_id: str,
    scene_active_profile_ids: set[str],
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> tuple[list[DocumentCreation], list[SeveredCreation]]:
    """Validate evidence, authority, uniqueness, and Event-Memory bindings for creations,
    then render each validated creation exactly once and return the results in order.

    A creation whose owner fails `_validate_owner_creation_authority` or
    `_validate_memory_creation_authority` with a `SeverableCreationAuthorityError`
    (owner is an active thread profile but not present in the current scene per
    `scene_active_profile_ids`) is dropped from the result instead of failing the
    whole Updater attempt; it is recorded in the returned severed list. Every other
    violation in this function — including the Event/Memory pairing cross-check that
    runs after severance — stays fatal and raises `WikiCommitPlanningError` as
    before."""
    existing_ids = {
        document.metadata.id
        for document in documents
        if document.metadata is not None
    }
    existing_paths = {document.path for document in documents}
    available_profile_ids = {
        document.metadata.profile_id
        for document in documents
        if document.metadata is not None
        and document.metadata.type == "character"
        and document.metadata.profile_id is not None
    }
    profile_documents_by_id: dict[str, WikiDocument] = {
        document.metadata.profile_id: document
        for document in documents
        if document.metadata is not None
        and document.metadata.type == "character"
        and document.metadata.profile_id is not None
    }
    # Memory 권한 검사는 existing_ids(전체 문서 id)가 아니라 event_ids(event 문서만)를
    # 써야 한다 — 그래야 검사를 통과한 related_event_id가 항상 실재하는 event를
    # 가리킨다고 보장할 수 있다.
    existing_event_ids = {
        document.metadata.id
        for document in documents
        if document.metadata is not None and document.metadata.type == "event"
    }
    created_event_ids = {
        creation.document_id
        for creation in creations
        if isinstance(creation, CreateEventDocument)
    }
    event_ids = existing_event_ids | created_event_ids
    event_titles = event_titles_by_id(documents, creations)
    seen_ids: set[str] = set()
    severed_ids: set[str] = set()
    severed_creations: list[SeveredCreation] = []
    prepared_creations: list[DocumentCreation] = []
    for creation in creations:
        source_text = user_input if creation.evidence_source == "player_input" else actor_response
        if not evidence_is_exact_quote(creation.evidence, source_text):
            raise WikiCommitPlanningError(
                "CreateDocument evidence is not an exact quote from "
                f"{creation.evidence_source}: {creation.document_id}"
            )
        if creation.confidence < 0.75:
            raise WikiCommitPlanningError(
                f"CreateDocument confidence is too low: {creation.document_id}"
            )
        if creation.document_id in existing_ids or creation.document_id in seen_ids:
            raise WikiCommitPlanningError(
                f"CreateDocument id already exists: {creation.document_id}"
            )
        document_slug = creation.document_id.split(":", 1)[1]
        directory = {
            "event": "events", "memory": "memories", "goal": "goals",
            "item": "items", "secret": "secrets",
        }[creation.document_type]
        candidate_path = f"{directory}/{document_slug}.md"
        if candidate_path in existing_paths:
            raise WikiCommitPlanningError(
                f"CreateDocument path already exists: {candidate_path}"
            )
        try:
            if isinstance(creation, CreateMemoryDocument):
                _validate_memory_creation_authority(
                    creation,
                    available_profile_ids=available_profile_ids,
                    scene_active_profile_ids=scene_active_profile_ids,
                    owner_document=profile_documents_by_id.get(creation.owner),
                    event_ids=event_ids,
                    event_titles=event_titles,
                    player_profile_id=player_profile_id,
                    actor_profile_id=actor_profile_id,
                )
            if isinstance(creation, (CreateGoalDocument, CreateItemDocument, CreateSecretDocument)):
                _validate_owner_creation_authority(
                    creation,
                    available_profile_ids=available_profile_ids,
                    scene_active_profile_ids=scene_active_profile_ids,
                    owner_document=profile_documents_by_id.get(creation.owner),
                    player_profile_id=player_profile_id,
                    actor_profile_id=actor_profile_id,
                )
        except SeverableCreationAuthorityError as exc:
            # Owner is an active thread profile but neither player nor Actor:
            # drop this one independent creation instead of failing the whole
            # attempt. Every other creation and every patch keeps validating.
            severed_ids.add(creation.document_id)
            severed_creations.append(
                SeveredCreation(
                    document_id=creation.document_id,
                    document_type=creation.document_type,
                    owner=creation.owner,
                    reason=str(exc),
                )
            )
            continue
        if isinstance(creation, CreateSecretDocument):
            unknown_knowers = [
                knower for knower in creation.knowers
                if knower not in available_profile_ids
            ]
            if unknown_knowers:
                raise WikiCommitPlanningError(
                    f"Secret knowers must be active thread profiles: {unknown_knowers}"
                )
        proposed_lines = _creation_proposed_lines(creation)
        if creation.evidence_source == "actor_response":
            player_conflicts = actor_source_player_conflicts(
                "", proposed_lines, player_reference_tokens
            )
            if player_conflicts:
                raise WikiCommitPlanningError(
                    "Actor-sourced CreateDocument cannot establish player action; "
                    f"conflicting lines: {' | '.join(player_conflicts[:3])}"
                )
        try:
            created = prepare_created_document(
                creation,
                thread_id,
                commit_id,
                created_at,
                user_message_id,
                assistant_message_id,
                related_event_title=(
                    event_titles[creation.related_event_id]
                    if isinstance(creation, CreateMemoryDocument)
                    else None
                ),
            )
        except ValueError as exc:
            raise WikiCommitPlanningError(
                f"CreateDocument could not be rendered: {creation.document_id}: {exc}"
            ) from exc
        _validate_actor_body_contract(
            document_body(created.content),
            WikiDocument(
                path=created.document,
                revision="validation",
                content=created.content,
                metadata=None,
            ),
            operation="CreateDocument",
        )
        seen_ids.add(creation.document_id)
        prepared_creations.append(created)
    created_memory_event_ids = {
        creation.related_event_id
        for creation in creations
        if isinstance(creation, CreateMemoryDocument)
        and creation.document_id not in severed_ids
    }
    missing_event_memory_ids = sorted(
        event_id for event_id in created_event_ids
        if event_id not in created_memory_event_ids
    )
    if missing_event_memory_ids:
        # A severed Memory can be the sole reason an Event now lacks a
        # matching Memory; that stays fatal for the whole attempt (severance
        # never widens what the Event/Memory pairing cross-check accepts).
        raise WikiCommitPlanningError(
            "Missing matching Memory for created Event(s): "
            f"{', '.join(missing_event_memory_ids)}. "
            "Each created Event requires at least one Memory created in the same "
            "response with `related_event_id` equal to the Event `document_id`."
        )
    return prepared_creations, severed_creations


def _creation_proposed_lines(creation: CreateDocument) -> str:
    """Collect narrative creation fields for actor-source player-action checks."""
    if isinstance(creation, CreateEventDocument):
        return "\n".join([
            creation.title, creation.occurred_at, creation.location,
            *creation.participants, *creation.witnesses, *creation.facts,
            *creation.direct_results, *creation.lasting_effects,
        ])
    if isinstance(creation, CreateMemoryDocument):
        return "\n".join([
            creation.title, creation.formation_trigger, creation.remembered_content,
            creation.interpretation, creation.emotion,
        ])
    if isinstance(creation, CreateGoalDocument):
        return "\n".join([
            creation.title, creation.desired_outcome, creation.success_look,
            creation.motivation, creation.current_step, creation.next_action,
            creation.obstacles, creation.completion_conditions,
        ])
    if isinstance(creation, CreateItemDocument):
        return "\n".join([
            creation.title, creation.kind, creation.appearance, creation.function,
            creation.constraint, creation.storage_location, creation.access_state,
            creation.recent_change,
        ])
    return "\n".join([
        creation.title, creation.actual_content, creation.who_knows,
        creation.concealment, creation.public_clue, creation.misunderstanding,
        creation.exposure_condition, creation.exposure_result,
    ])


def _validate_third_party_owner_authority(
    creation: CreateGoalDocument | CreateItemDocument | CreateSecretDocument | CreateMemoryDocument,
    *,
    label: str,
    owner_document: WikiDocument | None,
    scene_active_profile_ids: set[str],
) -> None:
    """Validate a creation owner who is neither the player nor the current Actor.

    Called only once the owner is confirmed to be a real active thread profile
    that is not the player and not the current Actor. Raises
    `SeverableCreationAuthorityError` (severable by the caller) when the owner
    is not present in the current scene per `scene_active_profile_ids` — a
    real character who simply is not part of this turn. Once the owner is
    confirmed scene-active, every further failure is fatal
    (`WikiCommitPlanningError`) rather than severable: a wrong evidence source
    or evidence that never names the owner both mean the model picked the
    wrong evidence for a real participant, not that the participant is
    absent, so they force a retry instead of silently dropping the creation.
    """
    if creation.owner not in scene_active_profile_ids:
        raise SeverableCreationAuthorityError(
            f"{label} owner is an active thread profile but not present in the current scene"
        )
    if creation.evidence_source != "actor_response":
        raise WikiCommitPlanningError(
            f"{label} owner {creation.owner} requires actor_response evidence"
        )
    owner_tokens = player_reference_tokens(owner_document)
    if owner_tokens and not any(token in creation.evidence for token in owner_tokens):
        raise WikiCommitPlanningError(
            f"{label} evidence does not name owner {creation.owner}: {creation.evidence!r}"
        )


def _validate_owner_creation_authority(
    creation: CreateGoalDocument | CreateItemDocument | CreateSecretDocument,
    *,
    available_profile_ids: set[str],
    scene_active_profile_ids: set[str],
    owner_document: WikiDocument | None,
    player_profile_id: str,
    actor_profile_id: str,
) -> None:
    """Validate active goal, item, or Secret owners and their evidence authority.

    Owner may be the player, the current Actor, or any other active thread
    character present in the current scene (see
    `_validate_third_party_owner_authority`). Raises
    `SeverableCreationAuthorityError` (severable by the caller) when the owner
    is an active thread profile but not in the current scene; every other
    failure here raises the plain, fatal `WikiCommitPlanningError`.
    """
    if creation.owner not in available_profile_ids:
        raise WikiCommitPlanningError(
            f"{creation.document_type} owner is not an active thread profile: {creation.owner}"
        )
    if creation.owner == actor_profile_id:
        if creation.evidence_source != "actor_response":
            raise WikiCommitPlanningError(
                f"{creation.document_type} owner {creation.owner} requires actor_response evidence"
            )
        return
    if creation.owner == player_profile_id:
        if creation.evidence_source != "player_input":
            raise WikiCommitPlanningError(
                f"{creation.document_type} owner {creation.owner} requires player_input evidence"
            )
        return
    _validate_third_party_owner_authority(
        creation,
        label=creation.document_type,
        owner_document=owner_document,
        scene_active_profile_ids=scene_active_profile_ids,
    )


def _validate_memory_creation_authority(
    creation: CreateMemoryDocument,
    *,
    available_profile_ids: set[str],
    scene_active_profile_ids: set[str],
    owner_document: WikiDocument | None,
    event_ids: set[str],
    event_titles: dict[str, str],
    player_profile_id: str,
    actor_profile_id: str,
) -> None:
    """Validate Memory ownership, source authority, and related Event availability.

    Passing this check means `creation.related_event_id in event_titles` is
    guaranteed to hold — the same dict a later lookup indexes to render the
    Memory body, so that lookup can never KeyError for a Memory that passed
    here. `event_ids` only sharpens the failure message; it never grants a
    pass on its own.

    Owner may be the player, the current Actor, or any other active thread
    character present in the current scene (see
    `_validate_third_party_owner_authority`). Raises
    `SeverableCreationAuthorityError` (severable by the caller) when the owner
    is an active thread profile but not in the current scene; every other
    failure here raises the plain, fatal `WikiCommitPlanningError`.
    """
    if creation.owner not in available_profile_ids:
        raise WikiCommitPlanningError(
            f"Memory owner is not an active thread profile: {creation.owner}"
        )
    if creation.related_event_id not in event_titles:
        if creation.related_event_id in event_ids:
            raise WikiCommitPlanningError(
                f"Memory related event has no title: {creation.related_event_id}"
            )
        raise WikiCommitPlanningError(
            f"Memory related event does not exist: {creation.related_event_id}"
        )
    if creation.owner == actor_profile_id:
        if creation.evidence_source != "actor_response":
            raise WikiCommitPlanningError(
                f"Memory owner {creation.owner} requires actor_response evidence"
            )
        return
    if creation.owner == player_profile_id:
        if creation.evidence_source != "player_input":
            raise WikiCommitPlanningError(
                f"Memory owner {creation.owner} requires player_input evidence"
            )
        return
    _validate_third_party_owner_authority(
        creation,
        label="Memory",
        owner_document=owner_document,
        scene_active_profile_ids=scene_active_profile_ids,
    )


def active_thread_id(documents: list[WikiDocument]) -> str:
    """Return the unique thread ID represented by Updater documents."""
    thread_ids = {
        document.metadata.thread_id
        for document in documents
        if document.metadata is not None and document.metadata.thread_id is not None
    }
    if len(thread_ids) != 1:
        raise WikiCommitPlanningError("Updater documents must belong to exactly one thread")
    return next(iter(thread_ids))


def event_titles_by_id(
    documents: list[WikiDocument],
    creations: list[CreateDocument],
) -> dict[str, str]:
    """Map existing and concurrently created Event IDs to visible titles."""
    titles: dict[str, str] = {}
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "event":
            continue
        title_match = _H1_RE.search(document.content)
        if title_match is not None:
            titles[metadata.id] = title_match.group(1).strip()
    titles.update({
        creation.document_id: creation.title
        for creation in creations
        if isinstance(creation, CreateEventDocument)
    })
    return titles
