# ================================
# src/wiki/document_creation.py
#
# 검증된 Updater event/memory 요청을 canonical Wiki Markdown 문서로 렌더링합니다.
#
# Functions
#   - prepare_created_document(request: CreateDocument, thread_id: str, commit_id: str, created_at: datetime, user_message_id: str | None = None, assistant_message_id: str | None = None, related_event_title: str | None = None) -> DocumentCreation : source turn이 연결된 canonical event/memory/goal/item/secret 문서를 만듭니다.
# ================================

from __future__ import annotations

from datetime import datetime
import json

from src.wiki.frontmatter import parse_frontmatter
from src.wiki.markdown import parse_markdown_sections
from src.wiki.models import (
    CreateDocument,
    CreateEventDocument,
    CreateGoalDocument,
    CreateItemDocument,
    CreateMemoryDocument,
    CreateSecretDocument,
    DocumentCreation,
)
from src.wiki.scaffold import render_wiki_template


def _yaml_value(value: str) -> str:
    """UTF-8 문자열을 JSON 호환 YAML scalar로 반환합니다."""
    return json.dumps(value, ensure_ascii=False)


def _joined(values: list[str], empty: str = "None.") -> str:
    """구조화된 단일 행 값 목록을 문서용 구분 문자열로 반환합니다."""
    return "; ".join(values) if values else empty


def _fact_lines(label: str, values: list[str], empty: str = "None.") -> str:
    """같은 canonical label을 유지하는 한 줄 bullet 목록을 반환합니다."""
    items = values or [empty]
    return "\n".join(f"- {label}: {item}" for item in items)


def _add_source_metadata(
    content: str,
    created_at: datetime,
    commit_id: str,
    user_message_id: str | None,
    assistant_message_id: str | None,
) -> str:
    """Rendered frontmatter에 source commit과 message ID를 추가합니다."""
    source_lines = [f"source_commit_id: {_yaml_value(commit_id)}"]
    if user_message_id:
        source_lines.append(
            f"source_user_message_id: {_yaml_value(user_message_id)}"
        )
    if assistant_message_id:
        source_lines.append(
            f"source_assistant_message_id: {_yaml_value(assistant_message_id)}"
        )
    created_at_line = "created_at: " + _yaml_value(created_at.isoformat())
    if content.count(created_at_line) != 1:
        raise ValueError("Created document has no unique created_at field")
    return content.replace(
        created_at_line,
        "\n".join([created_at_line, *source_lines]),
        1,
    )


def _replace_markers(
    content: str,
    replacements: dict[str, str],
    document_type: str,
) -> str:
    """Template marker를 정확히 한 번씩 canonical 값으로 교체합니다."""
    for original, replacement in replacements.items():
        if content.count(original) != 1:
            raise ValueError(
                f"{document_type} template marker is not unique: {original}"
            )
        content = content.replace(original, replacement, 1)
    return content


def _prepare_event_document(
    request: CreateEventDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> DocumentCreation:
    """Source commit/turn이 연결된 canonical event Markdown 생성을 반환합니다."""
    event_slug = request.document_id.removeprefix("event:")
    content = render_wiki_template(
        "event.md",
        {
            "DOCUMENT_ID": request.document_id,
            "THREAD_ID": thread_id,
            "TITLE": request.title,
            "CREATED_AT": created_at.isoformat(),
        },
    )
    content = _add_source_metadata(
        content,
        created_at,
        commit_id,
        user_message_id,
        assistant_message_id,
    )
    replacements = {
        "- 시각:": f"- 시각: {request.occurred_at}",
        "- 장소:": f"- 장소: {request.location}",
        "- 참여자:": f"- 참여자: {_joined(request.participants)}",
        "- 목격자:": f"- 목격자: {_joined(request.witnesses)}",
        "- 발생 내용:": _fact_lines("발생 내용", request.facts),
        "- 직접 결과:": _fact_lines("직접 결과", request.direct_results),
        "- 남은 영향:": _fact_lines("남은 영향", request.lasting_effects),
        "- 상태: concluded": f"- 상태: {request.status}",
        "- 진행 경과:": f"- 진행 경과: {request.progress}",
        "- 종료 시각:": f"- 종료 시각: {request.conclusion_time}",
    }
    content = _replace_markers(content, replacements, "Event")
    metadata = parse_frontmatter(content)
    parse_markdown_sections(content)
    if (
        metadata is None
        or metadata.type != "event"
        or metadata.thread_id != thread_id
        or metadata.id != request.document_id
    ):
        raise ValueError("Rendered event metadata does not match the active thread")
    return DocumentCreation(
        document=f"events/{event_slug}.md",
        content=content,
        evidence=request.evidence,
        evidence_source=request.evidence_source,
        confidence=request.confidence,
    )


def _prepare_memory_document(
    request: CreateMemoryDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None,
    assistant_message_id: str | None,
    related_event_title: str,
) -> DocumentCreation:
    """Source commit/turn이 연결된 owner-private memory Markdown 생성을 반환합니다."""
    memory_slug = request.document_id.removeprefix("memory:")
    content = render_wiki_template(
        "memory.md",
        {
            "DOCUMENT_ID": request.document_id,
            "THREAD_ID": thread_id,
            "OWNER_ID": request.owner,
            "TITLE": request.title,
            "CREATED_AT": created_at.isoformat(),
        },
    )
    content = _add_source_metadata(
        content,
        created_at,
        commit_id,
        user_message_id,
        assistant_message_id,
    )
    content = _replace_markers(
        content,
        {
            "- 관련 사건:": f"- 관련 사건: {related_event_title}",
            "- 형성 계기:": f"- 형성 계기: {request.formation_trigger}",
            "- 시각:": f"- 시각: {request.formed_at}",
            "- 장소:": f"- 장소: {request.location}",
            "- 기억 내용:": f"- 기억 내용: {request.remembered_content}",
            "- 해석:": f"- 해석: {request.interpretation}",
            "- 감정:": f"- 감정: {request.emotion}",
            "- 확신:": f"- 확신: {request.certainty}",
            "- 왜곡 가능성:": f"- 왜곡 가능성: {request.distortion_risk}",
        },
        "Memory",
    )
    metadata = parse_frontmatter(content)
    parse_markdown_sections(content)
    if (
        metadata is None
        or metadata.type != "memory"
        or metadata.thread_id != thread_id
        or metadata.id != request.document_id
        or metadata.owner != request.owner
    ):
        raise ValueError("Rendered memory metadata does not match the active thread")
    return DocumentCreation(
        document=f"memories/{memory_slug}.md",
        content=content,
        evidence=request.evidence,
        evidence_source=request.evidence_source,
        confidence=request.confidence,
    )


def _add_knowers_metadata(content: str, knowers: list[str]) -> str:
    """Secret frontmatter에 knower-scoping용 profile ID 목록을 추가합니다."""
    marker = "visibility: [actor, updater, player]"
    if content.count(marker) != 1:
        raise ValueError("Secret template visibility marker is not unique")
    knowers_line = "knowers: " + json.dumps(knowers, ensure_ascii=False)
    return content.replace(marker, "\n".join([marker, knowers_line]), 1)


def _prepare_goal_document(
    request: CreateGoalDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None,
    assistant_message_id: str | None,
) -> DocumentCreation:
    """Source commit/turn이 연결된 canonical goal Markdown 생성을 반환합니다."""
    goal_slug = request.document_id.removeprefix("goal:")
    content = render_wiki_template(
        "goal.md",
        {
            "DOCUMENT_ID": request.document_id,
            "THREAD_ID": thread_id,
            "OWNER_ID": request.owner,
            "TITLE": request.title,
            "CREATED_AT": created_at.isoformat(),
        },
    )
    content = _add_source_metadata(
        content,
        created_at,
        commit_id,
        user_message_id,
        assistant_message_id,
    )
    content = _replace_markers(
        content,
        {
            "- 원하는 결과:": f"- 원하는 결과: {request.desired_outcome}",
            "- 성공 모습:": f"- 성공 모습: {request.success_look}",
            "- 동기:": f"- 동기: {request.motivation}",
            "- 우선순위:": f"- 우선순위: {request.priority}",
            "- 상태: active": f"- 상태: {request.status}",
            "- 현재 단계:": f"- 현재 단계: {request.current_step}",
            "- 다음 행동:": f"- 다음 행동: {request.next_action}",
            "- 장애물:": f"- 장애물: {request.obstacles}",
            "- 완료 조건:": f"- 완료 조건: {request.completion_conditions}",
        },
        "Goal",
    )
    metadata = parse_frontmatter(content)
    parse_markdown_sections(content)
    if (
        metadata is None
        or metadata.type != "goal"
        or metadata.thread_id != thread_id
        or metadata.id != request.document_id
        or metadata.owner != request.owner
    ):
        raise ValueError("Rendered goal metadata does not match the active thread")
    return DocumentCreation(
        document=f"goals/{goal_slug}.md",
        content=content,
        evidence=request.evidence,
        evidence_source=request.evidence_source,
        confidence=request.confidence,
    )


def _prepare_item_document(
    request: CreateItemDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None,
    assistant_message_id: str | None,
) -> DocumentCreation:
    """Source commit/turn이 연결된 canonical item Markdown 생성을 반환합니다."""
    item_slug = request.document_id.removeprefix("item:")
    content = render_wiki_template(
        "item.md",
        {
            "DOCUMENT_ID": request.document_id,
            "THREAD_ID": thread_id,
            "OWNER_ID": request.owner,
            "TITLE": request.title,
            "CREATED_AT": created_at.isoformat(),
        },
    )
    content = _add_source_metadata(
        content,
        created_at,
        commit_id,
        user_message_id,
        assistant_message_id,
    )
    content = _replace_markers(
        content,
        {
            "- 종류:": f"- 종류: {request.kind}",
            "- 외형:": f"- 외형: {request.appearance}",
            "- 기능:": f"- 기능: {request.function}",
            "- 제약:": f"- 제약: {request.constraint}",
            "- 보관 장소:": f"- 보관 장소: {request.storage_location}",
            "- 접근 상태:": f"- 접근 상태: {request.access_state}",
            "- 상태: available": f"- 상태: {request.status}",
            "- 최근 변화:": f"- 최근 변화: {request.recent_change}",
        },
        "Item",
    )
    metadata = parse_frontmatter(content)
    parse_markdown_sections(content)
    if (
        metadata is None
        or metadata.type != "item"
        or metadata.thread_id != thread_id
        or metadata.id != request.document_id
        or metadata.owner != request.owner
    ):
        raise ValueError("Rendered item metadata does not match the active thread")
    return DocumentCreation(
        document=f"items/{item_slug}.md",
        content=content,
        evidence=request.evidence,
        evidence_source=request.evidence_source,
        confidence=request.confidence,
    )


def _prepare_secret_document(
    request: CreateSecretDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None,
    assistant_message_id: str | None,
) -> DocumentCreation:
    """Source commit/turn과 knower 목록이 연결된 canonical secret Markdown 생성을 반환합니다."""
    secret_slug = request.document_id.removeprefix("secret:")
    content = render_wiki_template(
        "secret.md",
        {
            "DOCUMENT_ID": request.document_id,
            "THREAD_ID": thread_id,
            "OWNER_ID": request.owner,
            "TITLE": request.title,
            "CREATED_AT": created_at.isoformat(),
        },
    )
    content = _add_source_metadata(
        content,
        created_at,
        commit_id,
        user_message_id,
        assistant_message_id,
    )
    if request.knowers:
        content = _add_knowers_metadata(content, request.knowers)
    content = _replace_markers(
        content,
        {
            "- 실제 내용:": f"- 실제 내용: {request.actual_content}",
            "- 알고 있는 인물:": f"- 알고 있는 인물: {request.who_knows}",
            "- 은폐 방식:": f"- 은폐 방식: {request.concealment}",
            "- 상태: hidden": f"- 상태: {request.status}",
            "- 공개 단서:": f"- 공개 단서: {request.public_clue}",
            "- 오해:": f"- 오해: {request.misunderstanding}",
            "- 발각 조건:": f"- 발각 조건: {request.exposure_condition}",
            "- 공개 결과:": f"- 공개 결과: {request.exposure_result}",
        },
        "Secret",
    )
    metadata = parse_frontmatter(content)
    parse_markdown_sections(content)
    if (
        metadata is None
        or metadata.type != "secret"
        or metadata.thread_id != thread_id
        or metadata.id != request.document_id
        or metadata.owner != request.owner
    ):
        raise ValueError("Rendered secret metadata does not match the active thread")
    return DocumentCreation(
        document=f"secrets/{secret_slug}.md",
        content=content,
        evidence=request.evidence,
        evidence_source=request.evidence_source,
        confidence=request.confidence,
    )


def prepare_created_document(
    request: CreateDocument,
    thread_id: str,
    commit_id: str,
    created_at: datetime,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    related_event_title: str | None = None,
) -> DocumentCreation:
    """요청 종류에 맞는 canonical 신규 Markdown 문서를 반환합니다."""
    if isinstance(request, CreateEventDocument):
        return _prepare_event_document(
            request,
            thread_id,
            commit_id,
            created_at,
            user_message_id,
            assistant_message_id,
        )
    if isinstance(request, CreateGoalDocument):
        return _prepare_goal_document(
            request,
            thread_id,
            commit_id,
            created_at,
            user_message_id,
            assistant_message_id,
        )
    if isinstance(request, CreateItemDocument):
        return _prepare_item_document(
            request,
            thread_id,
            commit_id,
            created_at,
            user_message_id,
            assistant_message_id,
        )
    if isinstance(request, CreateSecretDocument):
        return _prepare_secret_document(
            request,
            thread_id,
            commit_id,
            created_at,
            user_message_id,
            assistant_message_id,
        )
    if related_event_title is None:
        raise ValueError("Memory creation requires a related Event title")
    return _prepare_memory_document(
        request,
        thread_id,
        commit_id,
        created_at,
        user_message_id,
        assistant_message_id,
        related_event_title,
    )
