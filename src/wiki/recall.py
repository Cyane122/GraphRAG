# ================================
# src/wiki/recall.py
#
# 누적 Wiki 문서(event/memory/goal/item/secret)를 예산 내로 선택하는 결정적 recall입니다.
#
# 설계: 현재 장면·활성 캐릭터·활성 관계 등 구조적 필수 문서(ALWAYS_TYPES)는 항상 포함하고,
# 누적 문서만 예산을 초과할 때 (구조 관련성 -> 최근성) 순으로 상위를 남긴다. 예산 이하면
# 전체를 그대로 반환해 짧은 thread의 동작을 바꾸지 않는다. 랭킹 함수(`_recall_sort_key`)만
# 교체하면 임베딩 의미 검색으로 승격할 수 있는 seam이다.
#
# Functions
#   - estimate_recall_tokens(document: WikiDocument) -> int : 문서의 보수적인 prompt token 비용을 추정합니다.
#   - select_recall_documents(documents: list[WikiDocument], active_profile_ids: set[str], scene_text: str, budget: int, token_budget: int = -1) -> list[WikiDocument] : 누적 문서를 문서 수와 token 예산 내로 축소해 원래 순서로 반환합니다.
# ================================

from __future__ import annotations

from datetime import datetime, timezone
import math
import re

from src.wiki.models import WikiDocument

# 예산 대상이 되는 누적 문서 종류. 그 외(scene/character/relationship/thread/world 등)는 항상 포함.
ACCUMULATING_TYPES = frozenset({"event", "memory", "goal", "item", "secret"})

_H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def estimate_recall_tokens(document: WikiDocument) -> int:
    """Return a conservative prompt-token estimate for one Wiki document.

    Recall must stay provider-independent and deterministic, so this uses one
    token per three UTF-8 bytes instead of making a tokenizer or network call.
    That is deliberately conservative for English and close to one token per
    Korean character.
    """
    return max(1, math.ceil(len(document.content.encode("utf-8")) / 3))


def _document_title(document: WikiDocument) -> str:
    """문서 H1 제목을 반환하고 없으면 빈 문자열을 반환합니다."""
    match = _H1_RE.search(document.content)
    return match.group(1).strip() if match else ""


def _recall_sort_key(
    document: WikiDocument,
    active_profile_ids: set[str],
    scene_text: str,
) -> tuple[int, datetime]:
    """구조 관련성 점수와 생성 시각으로 정렬 키를 만듭니다(높을수록 우선).

    이 함수가 recall의 랭킹 seam이다. 임베딩 승격 시 여기만 유사도 기반으로 교체한다.
    """
    metadata = document.metadata
    score = 0
    if metadata is not None:
        if metadata.owner is not None and metadata.owner in active_profile_ids:
            score += 2
        knowers = set(getattr(metadata, "knowers", None) or [])
        if knowers & active_profile_ids:
            score += 2
        title = _document_title(document)
        if title and title in scene_text:
            score += 1
    created_at = (
        metadata.created_at
        if metadata is not None and metadata.created_at is not None
        else _EPOCH
    )
    return (score, created_at)


def select_recall_documents(
    documents: list[WikiDocument],
    active_profile_ids: set[str],
    scene_text: str,
    budget: int,
    token_budget: int = -1,
) -> list[WikiDocument]:
    """누적 문서를 문서 수와 token 예산 내로 줄여 원래 순서로 반환합니다."""
    accumulating = [
        document
        for document in documents
        if document.metadata is not None
        and document.metadata.type in ACCUMULATING_TYPES
    ]
    total_tokens = sum(estimate_recall_tokens(document) for document in accumulating)
    exceeds_document_budget = budget >= 0 and len(accumulating) > budget
    exceeds_token_budget = token_budget >= 0 and total_tokens > token_budget
    if not exceeds_document_budget and not exceeds_token_budget:
        return list(documents)
    ranked = sorted(
        accumulating,
        key=lambda document: _recall_sort_key(document, active_profile_ids, scene_text),
        reverse=True,
    )
    kept_paths: set[str] = set()
    used_tokens = 0
    for document in ranked:
        if budget >= 0 and len(kept_paths) >= budget:
            break
        document_tokens = estimate_recall_tokens(document)
        if token_budget >= 0 and used_tokens + document_tokens > token_budget:
            continue
        kept_paths.add(document.path)
        used_tokens += document_tokens
    return [
        document
        for document in documents
        if document.metadata is None
        or document.metadata.type not in ACCUMULATING_TYPES
        or document.path in kept_paths
    ]
