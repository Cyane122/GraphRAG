# ================================
# src/wiki/commit_planner.py
#
# 관련 Wiki 문서 전문에서 변경 섹션을 추출하고 재시도 후 지연 커밋을 만듭니다.
#
# Classes
#   - WikiCommitPlanningError : Wiki commit 계획 또는 재시도 소진 예외
#
# Functions
#   - _relationship_bullets(markdown: str) -> set[str] : 관계 변화 section의 bullet 행을 반환합니다.
#   - plan_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : 출처와 수정·생성 범위를 검증하고 시도별 진단 자료를 남기며 Wiki commit을 계획합니다.
#   - _synchronize_accepted_header(result: WikiUpdaterResult, documents: list[WikiDocument], user_input: str, actor_response: str) -> None : accepted Actor 헤더의 안전한 시각·장소를 scene patch에 결정적으로 반영합니다.
# ================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from difflib import ndiff
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from src.core.llm import extract_json_from_llm, get_model, get_response_text
from src.simulation.prose_headers import (
    parse_prose_header_datetime,
    parse_prose_header_location,
    parse_prose_header_text,
)
from src.wiki.context import scene_datetime_and_location
from src.wiki.document_creation import prepare_created_document
from src.wiki.markdown import (
    apply_section_patches,
    document_revision,
    parse_markdown_sections,
)
from src.wiki.models import (
    CreateDocument,
    CreateEventDocument,
    CreateGoalDocument,
    CreateItemDocument,
    CreateMemoryDocument,
    CreateSecretDocument,
    PendingWikiCommit,
    SectionPatch,
    WikiDocument,
    WikiUpdaterResult,
)
from src.wiki.updater_debug import (
    create_updater_debug_run,
    finish_updater_debug_run,
    write_updater_attempt_debug,
)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "updater.md"
_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_COLLECTIVE_PLAYER_MARKERS = ("두 사람", "두사람", "둘이", "둘은", "둘의")
_SHARED_PROPOSAL_MARKERS = ("가자고", "하자고", "제안", "요구", "부탁", "조르", "물었", "묻")
_SCENE_SECTION_ALIASES = ("현재 장면", "시작 기준")
_RELATIONSHIP_SECTION = "Relationship Development"
_RELATIONSHIP_EMPTY_SENTINEL = (
    "- No durable relationship change has occurred since the story began."
)
_TIME_PLACE_HEADING_PATTERN = (
    r"(?:Time and Place|시작 시각과 장소|현재 시각과 장소)"
)
_EXPLICIT_DATE_JUMP_RE = re.compile(
    r"다음\s*날|내일|모레|며칠\s*후|주일\s*후|주\s*후|달\s*후|"
    r"next\s+day|tomorrow|days?\s+later|weeks?\s+later",
    re.IGNORECASE,
)
_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class WikiCommitPlanningError(RuntimeError):
    """Wiki commit planner가 유효한 변경안을 만들지 못한 경우입니다."""


def _text_hash(text: str) -> str:
    """턴 입력과 Actor 응답의 안정적인 SHA-256 해시를 반환합니다."""
    return sha256(text.encode("utf-8")).hexdigest()


def _build_prompt(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    player_document: str | None,
    actor_document: str | None,
) -> str:
    """Updater 규칙과 관련 문서 전문을 하나의 요청으로 조립합니다."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    document_blocks = []
    for document in documents:
        document_blocks.append(
            "\n".join([
                f'<wiki_document path="{document.path}" revision="{document.revision}">',
                document.content,
                "</wiki_document>",
            ])
        )
    return "\n\n".join([
        template.strip(),
        "## Update Authority",
        f"- Player character document: {player_document or 'none'}",
        f"- Actor character document: {actor_document or 'none'}",
        "## Player Input",
        user_input,
        "## Accepted Actor Response",
        actor_response,
        "## Current Wiki Documents",
        "\n\n".join(document_blocks),
    ])


def _profile_document_path(
    documents: list[WikiDocument],
    profile_id: str,
) -> str | None:
    """profile ID에 대응하는 thread character 문서 경로를 반환합니다."""
    if not profile_id:
        return None
    for document in documents:
        metadata = document.metadata
        if (
            metadata is not None
            and metadata.type == "character"
            and metadata.profile_id == profile_id
        ):
            return document.path
    return None


def _evidence_is_exact_quote(evidence: str, source_text: str) -> bool:
    """바깥 인용부호만 제거한 evidence가 지정 원문에 그대로 존재하는지 반환합니다."""
    candidate = evidence.strip()
    quote_pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("`", "`"))
    for opening, closing in quote_pairs:
        if candidate.startswith(opening) and candidate.endswith(closing):
            candidate = candidate[len(opening):-len(closing)].strip()
            break
    return bool(candidate) and candidate in source_text


def _player_reference_tokens(document: WikiDocument | None) -> set[str]:
    """플레이어 문서 H1에서 전체 이름과 한국어 이름의 마지막 두 글자를 반환합니다."""
    if document is None:
        return set()
    match = _H1_RE.search(document.content)
    if match is None:
        return set()
    name = match.group(1).strip()
    tokens = {name}
    if len(name) >= 3 and all("가" <= char <= "힣" for char in name):
        tokens.add(name[-2:])
    return tokens


def _normalize_scene_section_alias(
    patch: SectionPatch,
    document: WikiDocument,
    section_paths: set[tuple[str, ...]],
) -> None:
    """scene H2 별칭만 실제 문서 제목으로 맞추고 replacement의 첫 제목도 동기화합니다."""
    metadata = document.metadata
    if (
        metadata is None
        or metadata.type != "scene"
        or len(patch.section_path) != 1
        or patch.section_path[0] not in _SCENE_SECTION_ALIASES
        or tuple(patch.section_path) in section_paths
    ):
        return
    existing_aliases = [
        alias for alias in _SCENE_SECTION_ALIASES if (alias,) in section_paths
    ]
    if len(existing_aliases) != 1:
        return
    actual_title = existing_aliases[0]
    replacement_heading = re.compile(
        rf"\A##\s+(?:{'|'.join(re.escape(alias) for alias in _SCENE_SECTION_ALIASES)})\s*$",
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


def _actor_source_player_conflicts(
    original_markdown: str,
    replacement_markdown: str,
    player_reference_tokens: set[str],
) -> list[str]:
    """Actor 근거 patch에서 새로 추가된 플레이어·공동 행동 줄을 반환합니다."""
    if not player_reference_tokens:
        return []
    added_lines = [
        line[2:].strip()
        for line in ndiff(
            original_markdown.splitlines(),
            replacement_markdown.splitlines(),
        )
        if line.startswith("+ ")
    ]
    conflicts: list[str] = []
    for line in added_lines:
        matched_tokens = sorted(
            marker for marker in _COLLECTIVE_PLAYER_MARKERS if marker in line
        )
        if (
            "함께" in line
            and not any(marker in line for marker in _SHARED_PROPOSAL_MARKERS)
        ):
            matched_tokens.append("함께")
        for token in sorted(player_reference_tokens):
            player_is_subject = re.search(
                rf"{re.escape(token)}\s*(?:은|는|이|가)(?=\s|[,.:;!?]|$)",
                line,
            )
            player_is_labeled_state = re.search(
                rf"(?:^|[-*]\s+){re.escape(token)}\s*:",
                line,
            )
            player_is_english_subject = re.search(
                rf"(?:^|[-*]\s+){re.escape(token)}\s+"
                r"(?:now\s+)?(?:is|was|has|had|feels|felt|believes|believed|"
                r"wants|wanted|trusts|trusted|loves|loved|hates|hated|"
                r"accepts|accepted|agrees|agreed|decides|decided|chooses|"
                r"chose|moves|moved|goes|went|does|did|says|said|thinks|"
                r"thought|consents|consented)\b",
                line,
                re.IGNORECASE,
            )
            if player_is_subject or player_is_labeled_state or player_is_english_subject:
                matched_tokens.append(token)
        if matched_tokens:
            conflicts.append(f"{', '.join(matched_tokens)} => {line}")
    return conflicts


def _relationship_bullets(markdown: str) -> set[str]:
    """관계 변화 H2 안의 정규화된 bullet 행을 반환합니다."""
    return {
        line.strip()
        for line in markdown.splitlines()
        if line.lstrip().startswith(("- ", "* "))
    }


def _validate_patch_policy(
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
) -> None:
    """출처 권한과 Wiki 문서별 canonical 수정 범위를 검증합니다."""
    source_text = (
        user_input if patch.evidence_source == "player_input" else actor_response
    )
    if not _evidence_is_exact_quote(patch.evidence, source_text):
        raise WikiCommitPlanningError(
            f"Updater evidence is not an exact quote from {patch.evidence_source}: "
            f"{patch.document}"
        )
    if patch.player_evidence is not None and not _evidence_is_exact_quote(
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
    section_path = tuple(patch.section_path)

    if metadata.type == "thread":
        raise WikiCommitPlanningError("Updater cannot modify thread management documents")

    if metadata.type == "scene":
        if len(section_path) != 1 or section_path[0] not in {"현재 장면", "시작 기준"}:
            raise WikiCommitPlanningError(
                "scene updates must replace the complete current-scene H2 section"
            )
        if patch.evidence_source == "actor_response":
            player_conflicts = _actor_source_player_conflicts(
                original_markdown,
                patch.replacement_markdown,
                player_reference_tokens,
            )
            if player_conflicts and patch.player_evidence is None:
                details = " | ".join(player_conflicts[:3])
                raise WikiCommitPlanningError(
                    "Actor-sourced scene patch can add player or shared movement only "
                    "when player_evidence is an exact quote from Player Input; "
                    f"conflicting added lines: {details}"
                )
    elif patch.player_evidence is not None:
        raise WikiCommitPlanningError(
            "player_evidence is supported only for complete current-scene patches"
        )

    if metadata.type == "relationship":
        if section_path != (_RELATIONSHIP_SECTION,):
            raise WikiCommitPlanningError(
                "relationship updates must replace the complete "
                "Relationship Development H2 section"
            )
        if metadata.owner != actor_profile_id:
            raise WikiCommitPlanningError(
                "relationship updates are limited to the active Actor-owned document"
            )
        if patch.evidence_source != "actor_response":
            raise WikiCommitPlanningError(
                "Actor-owned relationship changes require Actor Response evidence"
            )
        player_conflicts = _actor_source_player_conflicts(
            original_markdown,
            patch.replacement_markdown,
            player_reference_tokens,
        )
        if player_conflicts:
            details = " | ".join(player_conflicts[:3])
            raise WikiCommitPlanningError(
                "Actor-sourced relationship patch cannot establish player action "
                f"or internal state; conflicting added lines: {details}"
            )
        original_bullets = _relationship_bullets(original_markdown)
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
        return

    if metadata.type in {"goal", "item", "secret"}:
        allowed_root = {
            "goal": "진행 상태",
            "item": "현재 상태",
            "secret": "공개 상태",
        }[metadata.type]
        if not section_path or section_path[0] != allowed_root:
            raise WikiCommitPlanningError(
                f"{metadata.type} updates may modify only the '{allowed_root}' "
                f"section: {patch.document}"
            )
        if (
            metadata.owner == player_profile_id
            and patch.evidence_source != "player_input"
        ):
            raise WikiCommitPlanningError(
                f"Player-owned {metadata.type} state requires evidence from Player Input"
            )
        return

    if metadata.type == "event":
        raise WikiCommitPlanningError(
            f"Gameplay updater cannot patch immutable event documents: {patch.document}"
        )

    if metadata.type == "memory":
        raise WikiCommitPlanningError(
            "Gameplay updater cannot patch memory documents; gated memory distortion "
            f"owns their mutable section: {patch.document}"
        )

    if metadata.type != "character":
        return
    if not section_path or section_path[0] != "현재 상태":
        raise WikiCommitPlanningError(
            f"Gameplay updater cannot modify static character sections: {patch.document}"
        )
    if len(section_path) != 2:
        raise WikiCommitPlanningError(
            "Character gameplay updates must target one complete current-state H3"
        )
    if section_path[1] in {
        "욕구와 컨디션",
        "Personality Change Ledger",
        "Reproductive State",
    }:
        raise WikiCommitPlanningError(
            f"Runtime-owned character section cannot be patched by the gameplay model: "
            f"{section_path[1]}"
        )
    if patch.document == player_document and patch.evidence_source != "player_input":
        raise WikiCommitPlanningError(
            "Player character state requires evidence from Player Input"
        )
    if (
        patch.document in active_character_documents
        and section_path == ("현재 상태", "현재 위치와 활동")
    ):
        raise WikiCommitPlanningError(
            "Active character location/activity belongs in scene/current.md"
        )


def _validate_result(
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
) -> None:
    """모델 patch의 구조·근거·권한을 검증하고 대상 섹션 revision을 기록합니다."""
    by_path = {document.path: document for document in documents}
    if len(by_path) != len(documents):
        raise WikiCommitPlanningError("Updater input contains duplicate document paths")
    sections_by_path = {
        document.path: parse_markdown_sections(document.content)
        for document in documents
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
        _validate_patch_policy(
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
        )
        patch.base_section_revision = document_revision(section.markdown)
        patch.base_markdown = section.markdown
        grouped.setdefault(patch.document, []).append(patch)

    for path, patches in grouped.items():
        apply_section_patches(by_path[path], patches)
    _validate_document_creations(
        result.creations,
        documents,
        user_input=user_input,
        actor_response=actor_response,
        player_reference_tokens=player_reference_tokens,
        player_profile_id=player_profile_id,
        actor_profile_id=actor_profile_id,
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
) -> None:
    """새 event/memory의 근거·고유 ID·owner 권한 경계를 검증합니다."""
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
    created_event_ids = {
        creation.document_id
        for creation in creations
        if isinstance(creation, CreateEventDocument)
    }
    seen_ids: set[str] = set()
    for creation in creations:
        source_text = (
            user_input
            if creation.evidence_source == "player_input"
            else actor_response
        )
        if not _evidence_is_exact_quote(creation.evidence, source_text):
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
            "event": "events",
            "memory": "memories",
            "goal": "goals",
            "item": "items",
            "secret": "secrets",
        }[creation.document_type]
        candidate_path = f"{directory}/{document_slug}.md"
        if candidate_path in existing_paths:
            raise WikiCommitPlanningError(
                f"CreateDocument path already exists: {candidate_path}"
            )
        if isinstance(creation, CreateMemoryDocument):
            _validate_memory_creation_authority(
                creation,
                available_profile_ids=available_profile_ids,
                existing_ids=existing_ids,
                created_event_ids=created_event_ids,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
            )
        if isinstance(
            creation,
            (CreateGoalDocument, CreateItemDocument, CreateSecretDocument),
        ):
            _validate_owner_creation_authority(
                creation,
                available_profile_ids=available_profile_ids,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
            )
        if isinstance(creation, CreateSecretDocument):
            unknown_knowers = [
                knower
                for knower in creation.knowers
                if knower not in available_profile_ids
            ]
            if unknown_knowers:
                raise WikiCommitPlanningError(
                    f"Secret knowers must be active thread profiles: {unknown_knowers}"
                )
        proposed_lines = _creation_proposed_lines(creation)
        if creation.evidence_source == "actor_response":
            player_conflicts = _actor_source_player_conflicts(
                "",
                proposed_lines,
                player_reference_tokens,
            )
            if player_conflicts:
                raise WikiCommitPlanningError(
                    "Actor-sourced CreateDocument cannot establish player action; "
                    f"conflicting lines: {' | '.join(player_conflicts[:3])}"
                )
        seen_ids.add(creation.document_id)


def _creation_proposed_lines(creation: CreateDocument) -> str:
    """Actor-source 플레이어 행동 검사를 위해 생성 문서의 서술 줄을 모읍니다."""
    if isinstance(creation, CreateEventDocument):
        return "\n".join([
            creation.title,
            creation.occurred_at,
            creation.location,
            *creation.participants,
            *creation.witnesses,
            *creation.facts,
            *creation.direct_results,
            *creation.lasting_effects,
        ])
    if isinstance(creation, CreateMemoryDocument):
        return "\n".join([
            creation.title,
            creation.formation_trigger,
            creation.remembered_content,
            creation.interpretation,
            creation.emotion,
        ])
    if isinstance(creation, CreateGoalDocument):
        return "\n".join([
            creation.title,
            creation.desired_outcome,
            creation.success_look,
            creation.motivation,
            creation.current_step,
            creation.next_action,
            creation.obstacles,
            creation.completion_conditions,
        ])
    if isinstance(creation, CreateItemDocument):
        return "\n".join([
            creation.title,
            creation.kind,
            creation.appearance,
            creation.function,
            creation.constraint,
            creation.storage_location,
            creation.access_state,
            creation.recent_change,
        ])
    return "\n".join([
        creation.title,
        creation.actual_content,
        creation.who_knows,
        creation.concealment,
        creation.public_clue,
        creation.misunderstanding,
        creation.exposure_condition,
        creation.exposure_result,
    ])


def _validate_owner_creation_authority(
    creation: CreateGoalDocument | CreateItemDocument | CreateSecretDocument,
    *,
    available_profile_ids: set[str],
    player_profile_id: str,
    actor_profile_id: str,
) -> None:
    """goal/item/secret owner가 활성 profile이고 source 권한과 맞는지 검증합니다."""
    if creation.owner not in available_profile_ids:
        raise WikiCommitPlanningError(
            f"{creation.document_type} owner is not an active thread profile: {creation.owner}"
        )
    expected_source = (
        "actor_response"
        if creation.owner == actor_profile_id
        else "player_input"
        if creation.owner == player_profile_id
        else None
    )
    if expected_source is None:
        raise WikiCommitPlanningError(
            f"{creation.document_type} creation is limited to the active Actor or player profile"
        )
    if creation.evidence_source != expected_source:
        raise WikiCommitPlanningError(
            f"{creation.document_type} owner {creation.owner} requires {expected_source} evidence"
        )


def _validate_memory_creation_authority(
    creation: CreateMemoryDocument,
    *,
    available_profile_ids: set[str],
    existing_ids: set[str],
    created_event_ids: set[str],
    player_profile_id: str,
    actor_profile_id: str,
) -> None:
    """Memory owner, source authority와 관련 Event 존재를 검증합니다."""
    if creation.owner not in available_profile_ids:
        raise WikiCommitPlanningError(
            f"Memory owner is not an active thread profile: {creation.owner}"
        )
    if creation.related_event_id not in existing_ids | created_event_ids:
        raise WikiCommitPlanningError(
            f"Memory related event does not exist: {creation.related_event_id}"
        )
    expected_source = (
        "actor_response"
        if creation.owner == actor_profile_id
        else "player_input"
        if creation.owner == player_profile_id
        else None
    )
    if expected_source is None:
        raise WikiCommitPlanningError(
            "Memory creation is limited to the active Actor or player profile"
        )
    if creation.evidence_source != expected_source:
        raise WikiCommitPlanningError(
            f"Memory owner {creation.owner} requires {expected_source} evidence"
        )


def _active_thread_id(documents: list[WikiDocument]) -> str:
    """Updater 문서의 유일한 thread ID를 반환합니다."""
    thread_ids = {
        document.metadata.thread_id
        for document in documents
        if document.metadata is not None
        and document.metadata.thread_id is not None
    }
    if len(thread_ids) != 1:
        raise WikiCommitPlanningError(
            "Updater documents must belong to exactly one thread"
        )
    return next(iter(thread_ids))


def _event_titles_by_id(
    documents: list[WikiDocument],
    creations: list[CreateDocument],
) -> dict[str, str]:
    """기존·동시 생성 Event ID를 Actor-visible 표시 제목에 연결합니다."""
    titles: dict[str, str] = {}
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "event":
            continue
        title_match = _H1_RE.search(document.content)
        if title_match is not None:
            titles[metadata.id] = title_match.group(1).strip()
    titles.update(
        {
            creation.document_id: creation.title
            for creation in creations
            if isinstance(creation, CreateEventDocument)
        }
    )
    return titles


def _header_location_is_grounded(
    header_location: str,
    current_location: str,
    user_input: str,
) -> bool:
    """현재 장소이거나 사용자 입력이 구체적 장소 토큰을 언급했는지 반환합니다."""
    header_key = header_location.strip().casefold()
    if not header_key:
        return False
    if header_key == current_location.strip().casefold():
        return True
    input_key = user_input.casefold()
    candidates = [header_key]
    candidates.extend(
        token.strip(" ,，.。()[]")
        for token in re.split(r"[\s,，/]+", header_key)
        if len(token.strip(" ,，.。()[]")) >= 2
    )
    return any(candidate and candidate in input_key for candidate in candidates)


def _scene_time_place_line(scene_time: datetime, location: str) -> str:
    """Canonical English Time and Place bullet을 반환합니다."""
    weekday = _WEEKDAY_NAMES[scene_time.weekday()]
    month = _MONTH_NAMES[scene_time.month]
    return (
        f"- It is {scene_time:%H:%M} on {weekday}, {month} "
        f"{scene_time.day}, {scene_time.year}, in {location}."
    )


def _replace_scene_time_place(
    scene_markdown: str,
    replacement_line: str,
) -> str:
    """Complete scene H2 안의 Time and Place subsection을 교체하거나 추가합니다."""
    subsection = re.compile(
        rf"(?ms)(^### {_TIME_PLACE_HEADING_PATTERN}\s*$\n+).*?(?=^###\s+|\Z)"
    )
    if subsection.search(scene_markdown):
        return subsection.sub(
            lambda match: f"{match.group(1)}{replacement_line}\n\n",
            scene_markdown,
            count=1,
        ).rstrip()
    heading = re.compile(r"\A(##\s+.+?\s*$)", re.MULTILINE)
    if heading.search(scene_markdown) is None:
        raise WikiCommitPlanningError(
            "Current scene replacement has no complete H2 heading"
        )
    return heading.sub(
        lambda match: (
            f"{match.group(1)}\n\n### Time and Place\n\n"
            f"{replacement_line}"
        ),
        scene_markdown,
        count=1,
    ).rstrip()


def _synchronize_accepted_header(
    result: WikiUpdaterResult,
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
) -> None:
    """Accepted 헤더의 안전한 시간·장소를 complete scene patch에 병합합니다."""
    header_text = parse_prose_header_text(actor_response)
    header_time = parse_prose_header_datetime(actor_response)
    header_location = parse_prose_header_location(actor_response)
    if not header_text or header_time is None:
        return
    scene_document = next(
        (
            document
            for document in documents
            if document.metadata is not None
            and document.metadata.type == "scene"
        ),
        None,
    )
    if scene_document is None:
        raise WikiCommitPlanningError("Updater input has no current scene document")
    current_time, current_location = scene_datetime_and_location(
        scene_document.content
    )
    safe_time = current_time
    if header_time >= current_time and (
        header_time.date() == current_time.date()
        or _EXPLICIT_DATE_JUMP_RE.search(user_input)
    ):
        safe_time = header_time
    safe_location = current_location
    if header_location and _header_location_is_grounded(
        header_location,
        current_location,
        user_input,
    ):
        safe_location = header_location
    if (
        safe_time == current_time
        and safe_location.strip().casefold() == current_location.strip().casefold()
    ):
        return

    sections = parse_markdown_sections(scene_document.content)
    aliases = [
        alias for alias in _SCENE_SECTION_ALIASES if (alias,) in sections
    ]
    if len(aliases) != 1:
        raise WikiCommitPlanningError(
            "Current scene must contain exactly one canonical scene H2"
        )
    section_path = (aliases[0],)
    existing_patch = next(
        (
            patch
            for patch in result.patches
            if patch.document == scene_document.path
        ),
        None,
    )
    base_section = sections[section_path]
    target_markdown = (
        existing_patch.replacement_markdown
        if existing_patch is not None
        else base_section.markdown
    )
    replacement = _replace_scene_time_place(
        target_markdown,
        _scene_time_place_line(safe_time, safe_location),
    )
    if replacement.rstrip() == target_markdown.rstrip():
        return
    if existing_patch is not None:
        existing_patch.replacement_markdown = replacement
        apply_section_patches(scene_document, [existing_patch])
    else:
        deterministic_patch = SectionPatch(
            document=scene_document.path,
            base_revision=scene_document.revision,
            base_section_revision=document_revision(base_section.markdown),
            base_markdown=base_section.markdown,
            section_path=section_path,
            replacement_markdown=replacement,
            evidence=header_text,
            evidence_source="actor_response",
            confidence=1.0,
        )
        apply_section_patches(scene_document, [deterministic_patch])
        result.patches.append(deterministic_patch)
    suffix = "Accepted Actor header time/location synchronized."
    result.summary = f"{result.summary.rstrip()} {suffix}".strip()


async def plan_pending_commit(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    model_name: str,
    max_attempts: int = 3,
    player_profile_id: str = "",
    actor_profile_id: str = "",
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    thinking_level: str | None = None,
    debug_root: Path | None = None,
) -> PendingWikiCommit:
    """Canonical 수정·생성 범위를 검증하고 진단 자료를 남기며 재시도합니다."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    player_document = _profile_document_path(documents, player_profile_id)
    actor_document = _profile_document_path(documents, actor_profile_id)
    if player_profile_id and player_document is None:
        raise WikiCommitPlanningError(f"Player character document is missing: {player_profile_id}")
    if actor_profile_id and actor_document is None:
        raise WikiCommitPlanningError(f"Actor character document is missing: {actor_profile_id}")
    documents_by_path = {document.path: document for document in documents}
    player_reference_tokens = _player_reference_tokens(
        documents_by_path.get(player_document) if player_document is not None else None
    )
    active_character_documents = {
        path for path in (player_document, actor_document) if path is not None
    }
    model = get_model(
        model_name,
        system_prompt=(Path(__file__).parent / "prompts" / "updater_system.md").read_text(
            encoding="utf-8"
        ),
    )
    prompt = _build_prompt(
        documents,
        user_input,
        actor_response,
        player_document,
        actor_document,
    )
    user_input_hash = _text_hash(user_input)
    actor_response_hash = _text_hash(actor_response)
    debug_run = await asyncio.to_thread(
        create_updater_debug_run,
        debug_root,
        model_name,
        max_attempts,
        user_input_hash,
        actor_response_hash,
    )
    last_error: Exception | None = None
    rejection_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        response_text = ""
        try:
            attempt_prompt = prompt
            if rejection_errors:
                rejection_history = "\n".join(
                    f"- {error}" for error in rejection_errors
                )
                attempt_prompt = "\n\n".join([
                    prompt,
                    "## Previous Attempt Rejected",
                    rejection_history,
                    (
                        "Every rejection above remains in force. Do not reintroduce a "
                        "problem fixed after an earlier attempt."
                    ),
                    "Return a corrected JSON result that satisfies every rule.",
                ])
            generation_config: dict[str, object] = {
                "temperature": 0.0,
                "max_output_tokens": 65536,
                "response_mime_type": "application/json",
                "log_source": "wiki_updater",
            }
            if thinking_level is not None:
                generation_config["thinking_config"] = {
                    "thinking_level": thinking_level
                }
            response = await model.generate_content_async(
                attempt_prompt,
                generation_config=generation_config,
            )
            response_text = get_response_text(response)
            payload = extract_json_from_llm(
                response_text,
                source="wiki_updater",
                strict=True,
            )
            result = WikiUpdaterResult.model_validate(payload)
            _validate_result(
                result,
                documents,
                user_input=user_input,
                actor_response=actor_response,
                player_document=player_document,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
                player_reference_tokens=player_reference_tokens,
                active_character_documents=active_character_documents,
            )
            _synchronize_accepted_header(
                result,
                documents,
                user_input,
                actor_response,
            )
            await asyncio.to_thread(
                write_updater_attempt_debug,
                debug_run,
                attempt,
                attempt_prompt,
                response_text,
                None,
            )
            await asyncio.to_thread(
                finish_updater_debug_run,
                debug_run,
                "accepted",
                attempt,
            )
            commit_id = uuid4().hex
            created_at = datetime.now(timezone.utc)
            thread_id = _active_thread_id(documents)
            event_titles = _event_titles_by_id(documents, result.creations)
            creations = [
                prepare_created_document(
                    creation,
                    thread_id,
                    commit_id,
                    created_at,
                    user_message_id,
                    assistant_message_id,
                    (
                        event_titles[creation.related_event_id]
                        if isinstance(creation, CreateMemoryDocument)
                        else None
                    ),
                )
                for creation in result.creations
            ]
            return PendingWikiCommit(
                commit_id=commit_id,
                created_at=created_at,
                user_input_hash=user_input_hash,
                actor_response_hash=actor_response_hash,
                updater_model=model_name,
                updater_attempts=attempt,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                summary=result.summary,
                patches=result.patches,
                creations=creations,
            )
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            if error_text not in rejection_errors:
                rejection_errors.append(error_text)
            await asyncio.to_thread(
                write_updater_attempt_debug,
                debug_run,
                attempt,
                attempt_prompt,
                response_text,
                str(exc),
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))

    final_error = f"Wiki updater failed after {max_attempts} attempts: {last_error}"
    await asyncio.to_thread(
        finish_updater_debug_run,
        debug_run,
        "failed",
        max_attempts,
        final_error,
    )
    raise WikiCommitPlanningError(final_error) from last_error
