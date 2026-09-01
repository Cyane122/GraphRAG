# ================================
# src/wiki/postprocess.py
#
# 정상 단일 Updater 뒤에 게이트가 켜졌을 때만 실행되는 Wiki 실험적 postprocessor입니다.
# 각 postprocessor는 추가 LLM 호출로 변경을 만들어 같은 pending commit에 충돌 없이 병합합니다.
# 기본은 전부 off이므로 정상 경로는 단일 Updater 호출 하나로 유지됩니다.
#
# Functions
#   - apply_wiki_postprocessors(documents, user_input, actor_response, pending, actor_profile_id, player_profile_id, model_name, wiki_systems: dict[str, bool] | None = None) -> str | None : 활성화된 postprocessor를 실행해 pending에 병합하고 OOC 메시지를 반환합니다.
#   - plan_memory_distortion(documents, actor_response, actor_profile_id, model_name) -> list[SectionPatch] : 활성 NPC 기억의 해석·감정 왜곡 patch를 계획합니다.
#   - plan_gossip(documents, actor_response, pending, actor_profile_id, player_profile_id, model_name) -> list[DocumentCreation] : 새 event 목격자의 주관적 memory 생성을 계획합니다.
# ================================

from __future__ import annotations

import logging
from pathlib import Path
import re

from src.core.llm import extract_json_from_llm, get_model, get_response_text
from src.wiki.document_creation import prepare_created_document
from src.wiki.evidence import document_body, first_nonempty_line
from src.wiki.frontmatter import parse_frontmatter
from src.wiki.models import (
    CreateMemoryDocument,
    DocumentCreation,
    PendingWikiCommit,
    SectionPatch,
    WikiDocument,
)
from src.wiki.patches import build_actor_response_section_patch

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent / "prompts"
_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _single_line(value: object) -> str:
    """모델 값이 비어 있지 않은 한 줄이면 정규화해 반환하고 아니면 빈 문자열을 반환합니다."""
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        return ""
    return text


def _document_title(content: str) -> str:
    """Markdown 본문의 H1 제목을 반환합니다."""
    match = _H1_RE.search(content)
    return match.group(1).strip() if match else ""


def _thread_id_of(documents: list[WikiDocument]) -> str | None:
    """문서들의 공통 thread_id를 반환하고 하나가 아니면 None을 반환합니다."""
    thread_ids = {
        document.metadata.thread_id
        for document in documents
        if document.metadata is not None and document.metadata.thread_id is not None
    }
    return next(iter(thread_ids)) if len(thread_ids) == 1 else None


async def _call_json(model_name: str, system_prompt: str, content: str) -> dict:
    """postprocessor 프롬프트로 구조화 JSON을 한 번 호출해 반환합니다."""
    model = get_model(model_name, system_prompt=system_prompt)
    response = await model.generate_content_async(
        content,
        generation_config={
            "temperature": 0.4,
            "max_output_tokens": 8192,
            "response_mime_type": "application/json",
            "log_source": "wiki_postprocess",
        },
    )
    payload = extract_json_from_llm(
        get_response_text(response),
        source="wiki_postprocess",
        strict=True,
    )
    return payload if isinstance(payload, dict) else {}


def _merge_patches(
    pending: PendingWikiCommit,
    patches: list[SectionPatch],
    *,
    replace_exact: bool = False,
) -> None:
    """겹치지 않는 patch를 병합하고 선택적으로 같은 정확한 대상을 교체합니다."""
    existing = {(patch.document, tuple(patch.section_path)) for patch in pending.patches}
    for patch in patches:
        key = (patch.document, tuple(patch.section_path))
        if key in existing:
            if replace_exact:
                pending.patches = [
                    current
                    for current in pending.patches
                    if (current.document, tuple(current.section_path)) != key
                ]
                pending.patches.append(patch)
            continue
        existing.add(key)
        pending.patches.append(patch)


def _merge_creations(
    pending: PendingWikiCommit,
    creations: list[DocumentCreation],
) -> None:
    """같은 경로 문서가 이미 있으면 건너뛰고 나머지만 병합합니다."""
    existing = {creation.document for creation in pending.creations}
    for creation in creations:
        if creation.document in existing:
            continue
        existing.add(creation.document)
        pending.creations.append(creation)


def _resolved_wiki_systems(wiki_systems: dict[str, bool] | None) -> dict[str, bool]:
    """호출값이 없으면 env 기본값으로 보완한 Wiki system 표를 반환합니다."""
    from src.config import wiki_system_defaults

    resolved = wiki_system_defaults()
    if wiki_systems is not None:
        resolved.update(wiki_systems)
    return resolved


async def plan_memory_distortion(
    documents: list[WikiDocument],
    actor_response: str,
    actor_profile_id: str,
    model_name: str,
) -> list[SectionPatch]:
    """활성 NPC 기억의 해석·감정만 왜곡하는 patch를 계획합니다(사실은 보존)."""
    evidence = first_nonempty_line(actor_response)
    if not evidence:
        return []
    memories = [
        document
        for document in documents
        if document.metadata is not None
        and document.metadata.type == "memory"
        and document.metadata.owner == actor_profile_id
    ]
    if not memories:
        return []
    system_prompt = (_PROMPT_DIR / "distortion.md").read_text(encoding="utf-8")
    blocks = [
        f'<memory id="{memory.metadata.id}">\n{document_body(memory.content)}\n</memory>'
        for memory in memories
    ]
    content = "\n\n".join([
        "## Accepted Turn",
        actor_response,
        "## NPC Memories",
        "\n\n".join(blocks),
    ])
    payload = await _call_json(model_name, system_prompt, content)
    by_id = {memory.metadata.id: memory for memory in memories}
    patches: list[SectionPatch] = []
    for item in payload.get("distortions", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        memory = by_id.get(str(item.get("memory_id") or ""))
        interpretation = _single_line(item.get("interpretation"))
        emotion = _single_line(item.get("emotion"))
        if memory is None or not interpretation or not emotion:
            continue
        replacement = (
            "### 해석과 감정\n\n"
            f"- 해석: {interpretation}\n"
            f"- 감정: {emotion}"
        )
        patch = build_actor_response_section_patch(
            memory,
            ("주관적 기억", "해석과 감정"),
            replacement,
            evidence=evidence,
        )
        if patch is not None:
            patches.append(patch)
    return patches


def _witness_names(event_content: str) -> list[str]:
    """Event 본문의 목격자 목록을 이름 리스트로 반환합니다."""
    match = re.search(r"(?m)^- 목격자:\s*(.+?)\s*$", event_content)
    if match is None:
        return []
    raw = match.group(1).strip()
    if raw in {"", "None."}:
        return []
    return [name.strip() for name in raw.split(";") if name.strip()]


def _event_field(event_content: str, label: str) -> str:
    """Event 본문에서 `- {label}: ` 한 줄 값을 반환합니다."""
    match = re.search(rf"(?m)^- {re.escape(label)}:\s*(.+?)\s*$", event_content)
    return match.group(1).strip() if match else ""


async def plan_gossip(
    documents: list[WikiDocument],
    actor_response: str,
    pending: PendingWikiCommit,
    actor_profile_id: str,
    player_profile_id: str,
    model_name: str,
) -> list[DocumentCreation]:
    """새로 생성된 event의 목격자(활성 인물 제외)에게 주관적 memory를 만듭니다."""
    evidence = first_nonempty_line(actor_response)
    if not evidence:
        return []
    thread_id = _thread_id_of(documents)
    if thread_id is None:
        return []
    events = [
        (parse_frontmatter(creation.content), creation.content)
        for creation in pending.creations
    ]
    events = [
        (metadata.id, content)
        for metadata, content in events
        if metadata is not None and metadata.type == "event"
    ]
    if not events:
        return []
    name_to_profile: dict[str, str] = {}
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "character" or metadata.profile_id is None:
            continue
        title = _document_title(document.content)
        if title:
            name_to_profile[title] = metadata.profile_id
    existing_ids = {
        document.metadata.id
        for document in documents
        if document.metadata is not None
    }
    for creation in pending.creations:
        metadata = parse_frontmatter(creation.content)
        if metadata is not None:
            existing_ids.add(metadata.id)
    system_prompt = (_PROMPT_DIR / "gossip.md").read_text(encoding="utf-8")
    creations_out: list[DocumentCreation] = []
    for event_id, event_content in events:
        title = _document_title(event_content)
        eligible = {
            name: name_to_profile[name]
            for name in _witness_names(event_content)
            if name in name_to_profile
            and name_to_profile[name] not in {actor_profile_id, player_profile_id}
        }
        if not eligible:
            continue
        content = "\n\n".join([
            "## Accepted Turn",
            actor_response,
            "## Created Event",
            event_content,
            "## Eligible Witnesses",
            ", ".join(eligible),
        ])
        payload = await _call_json(model_name, system_prompt, content)
        occurred_at = _event_field(event_content, "시각") or "장면 시각 참조"
        location = _event_field(event_content, "장소") or "현재 장소"
        for item in payload.get("witness_memories", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            witness_name = str(item.get("witness_name") or "")
            profile = eligible.get(witness_name)
            remembered = _single_line(item.get("remembered_content"))
            interpretation = _single_line(item.get("interpretation"))
            emotion = _single_line(item.get("emotion"))
            certainty = _single_line(item.get("certainty"))
            distortion_risk = _single_line(item.get("distortion_risk"))
            if profile is None or not all(
                [remembered, interpretation, emotion, certainty, distortion_risk]
            ):
                continue
            slug = _SLUG_RE.sub(
                "-",
                f"{event_id.split(':', 1)[1]}-{profile.split(':', 1)[1]}",
            ).strip("-")[:63]
            document_id = f"memory:{slug}"
            if not slug or document_id in existing_ids:
                continue
            try:
                memory = CreateMemoryDocument(
                    document_id=document_id,
                    title=f"{witness_name} witnessed {title}"[:120],
                    owner=profile,
                    related_event_id=event_id,
                    formation_trigger=f"Witnessing {title}"[:240],
                    formed_at=occurred_at[:160],
                    location=location[:160],
                    remembered_content=remembered[:600],
                    interpretation=interpretation[:400],
                    emotion=emotion[:200],
                    certainty=certainty[:200],
                    distortion_risk=distortion_risk[:240],
                    evidence=evidence,
                    evidence_source="actor_response",
                    confidence=0.8,
                )
                rendered = prepare_created_document(
                    memory,
                    thread_id,
                    pending.commit_id,
                    pending.created_at,
                    pending.user_message_id,
                    pending.assistant_message_id,
                    related_event_title=title,
                )
            except Exception as exc:
                logger.warning(f"[WikiGossip] witness memory skipped: {exc}")
                continue
            existing_ids.add(document_id)
            creations_out.append(rendered)
    return creations_out


async def apply_wiki_postprocessors(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    pending: PendingWikiCommit,
    actor_profile_id: str,
    player_profile_id: str,
    model_name: str,
    wiki_systems: dict[str, bool] | None = None,
) -> str | None:
    """활성화된 gated postprocessor를 best-effort로 병합하고 OOC 메시지를 반환합니다."""
    from src.wiki.character_postprocess import (
        plan_organic_state,
        plan_personality_drift,
    )
    from src.wiki.needs import plan_needs_decay

    del user_input

    systems = _resolved_wiki_systems(wiki_systems)
    ooc_message: str | None = None
    try:
        needs_patches = plan_needs_decay(
            documents,
            actor_response,
            actor_profile_id,
        )
        _merge_patches(pending, needs_patches, replace_exact=True)
    except Exception as exc:
        logger.warning(f"[WikiPostprocess] deterministic needs failed (ignored): {exc}")

    document_types = {
        document.path: document.metadata.type
        for document in documents
        if document.metadata is not None
    }
    has_relationship_change = any(
        document_types.get(patch.document) == "relationship"
        for patch in pending.patches
    )
    if systems["memory_distortion"] and actor_profile_id and has_relationship_change:
        try:
            patches = await plan_memory_distortion(
                documents, actor_response, actor_profile_id, model_name
            )
            _merge_patches(pending, patches)
        except Exception as exc:
            logger.warning(f"[WikiPostprocess] memory distortion failed (ignored): {exc}")

    if systems["gossip"]:
        try:
            creations = await plan_gossip(
                documents,
                actor_response,
                pending,
                actor_profile_id,
                player_profile_id,
                model_name,
            )
            _merge_creations(pending, creations)
        except Exception as exc:
            logger.warning(f"[WikiPostprocess] gossip failed (ignored): {exc}")

    if systems["personality_drift"]:
        try:
            personality_patches = await plan_personality_drift(
                documents,
                actor_response,
                pending,
                actor_profile_id,
                model_name,
            )
            _merge_patches(pending, personality_patches)
        except Exception as exc:
            logger.warning(f"[WikiPostprocess] personality drift failed (ignored): {exc}")

    if systems["pregnancy"]:
        try:
            organic_patches, ooc_message = await plan_organic_state(
                documents,
                actor_response,
                pending,
                actor_profile_id,
                player_profile_id,
                model_name,
            )
            _merge_patches(pending, organic_patches, replace_exact=True)
        except Exception as exc:
            logger.warning(f"[WikiPostprocess] organic state failed (ignored): {exc}")
    return ooc_message
