# ================================
# src/wiki/prompt_contract.py
#
# Wiki 문서 본문과 컴파일된 Actor prompt의 노출·세그먼트 계약을 검증합니다.
#
# Classes
#   - WikiPromptContractError : Actor prompt의 메타데이터·세그먼트 경계 위반 예외
#
# Functions
#   - validate_actor_document_body(body: str, document: WikiDocument) -> None : Actor-visible Wiki 문서 본문의 독립성을 검증합니다.
#   - validate_wiki_prompt_bundle(bundle: WikiPromptBundle) -> None : 컴파일된 Actor prompt의 메타데이터·세그먼트 계약을 검증합니다.
# ================================

from __future__ import annotations

import re

from src.wiki.models import WikiDocument, WikiPromptBundle


_ACTOR_BARE_FILE_REFERENCE_RE = re.compile(
    r"(?<![\w.-])[\w.-]+\.md\b",
    re.IGNORECASE,
)
_ACTOR_WIKILINK_RE = re.compile(r"\[\[")
_ACTOR_FRONTMATTER_FIELD_RE = re.compile(
    r"^\s*(?:id|type|schema_version|visibility|created_at|world_id|thread_id|"
    r"profile_id|owner|participants|tags|aliases|pov_mode|rating|scene_type|description)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


class WikiPromptContractError(RuntimeError):
    """Actor prompt에 저장소 메타데이터가 새거나 세그먼트 경계가 깨진 경우입니다."""


def validate_actor_document_body(body: str, document: WikiDocument) -> None:
    """단일 Wiki 문서 본문이 Actor에 안전한 독립 prompt 모듈인지 검증합니다."""
    violations: list[str] = []
    if _ACTOR_WIKILINK_RE.search(body):
        violations.append("wikilink")
    if _ACTOR_BARE_FILE_REFERENCE_RE.search(body):
        violations.append("Markdown file reference")
    if _ACTOR_FRONTMATTER_FIELD_RE.search(body):
        violations.append("frontmatter field")
    if violations:
        joined = ", ".join(violations)
        raise WikiPromptContractError(
            f"Actor-visible Wiki document {document.path!r} contains {joined}"
        )


def validate_wiki_prompt_bundle(bundle: WikiPromptBundle) -> None:
    """컴파일된 prompt의 필수 태그 수와 Fixed/Genre/Dynamic 배치를 검증합니다."""
    if not bundle.fixed_prompt.strip():
        raise WikiPromptContractError("Wiki Fixed prompt must not be empty")
    if not bundle.dynamic_prompt.strip():
        raise WikiPromptContractError("Wiki Dynamic prompt must not be empty")

    required_tags = (
        ("fixed", bundle.fixed_prompt, "world_specific_prose_prompt"),
        ("fixed", bundle.fixed_prompt, "prose_rules"),
        ("dynamic", bundle.dynamic_prompt, "current_scene"),
        ("dynamic", bundle.dynamic_prompt, "user_input"),
    )
    for segment_name, segment, tag in required_tags:
        opening = f"<{tag}>"
        closing = f"</{tag}>"
        if segment.count(opening) != 1 or segment.count(closing) != 1:
            raise WikiPromptContractError(
                f"Wiki {segment_name} prompt must contain exactly one {opening} block"
            )
        if segment.index(opening) >= segment.index(closing):
            raise WikiPromptContractError(
                f"Wiki {segment_name} prompt has an invalid {opening} block"
            )

    prose_wrapper_start = bundle.fixed_prompt.index(
        "<world_specific_prose_prompt>"
    )
    prose_wrapper_end = bundle.fixed_prompt.index(
        "</world_specific_prose_prompt>"
    )
    prose_rules_start = bundle.fixed_prompt.index("<prose_rules>")
    prose_rules_end = bundle.fixed_prompt.index("</prose_rules>")
    if not (
        prose_wrapper_start
        < prose_rules_start
        < prose_rules_end
        < prose_wrapper_end
    ):
        raise WikiPromptContractError(
            "Wiki prose rules must be nested in the Fixed prose wrapper"
        )
    if "<current_" in bundle.fixed_prompt or "<current_" in bundle.genre_prompt:
        raise WikiPromptContractError(
            "Mutable Wiki state must remain in the Dynamic prompt"
        )
    if (
        "<world_specific_prose_prompt>" in bundle.genre_prompt
        or "<world_specific_prose_prompt>" in bundle.dynamic_prompt
        or "<prose_rules>" in bundle.genre_prompt
        or "<prose_rules>" in bundle.dynamic_prompt
    ):
        raise WikiPromptContractError(
            "Wiki prose rules must appear only in the Fixed prompt"
        )
    if "<current_scene>" in bundle.genre_prompt:
        raise WikiPromptContractError(
            "Wiki current scene must appear only in the Dynamic prompt"
        )
    if _ACTOR_WIKILINK_RE.search(bundle.fixed_prompt):
        raise WikiPromptContractError("Wiki Fixed prompt contains a wikilink")
    if _ACTOR_WIKILINK_RE.search(bundle.genre_prompt):
        raise WikiPromptContractError("Wiki Genre prompt contains a wikilink")
