# ================================
# src/wiki/evidence.py
#
# Provides shared evidence-line and exact-quote checks for Wiki commit planning, plus the
# neutral name-token and scene-membership helpers `context.py` and `commit_policy.py` both
# need (kept here, not in either of those modules, to avoid an import cycle between them).
#
# Functions
#   - evidence_is_exact_quote(evidence: str, source_text: str) -> bool : Return whether evidence is an exact source quote after removing one surrounding quote pair.
#   - first_nonempty_line(text: str) -> str : Return the first nonempty stripped line from text.
#   - actor_source_player_conflicts(original_markdown: str, replacement_markdown: str, player_reference_tokens: set[str]) -> list[str] : Return added actor-source lines that establish player or joint action.
#   - document_body(content: str) -> str : Return the Markdown body with YAML frontmatter removed.
#   - player_reference_tokens(document: WikiDocument | None) -> set[str] : Return full and Korean-short name tokens from a character document's H1.
#   - scene_active_profile_ids(documents: list[WikiDocument]) -> set[str] : Return profile IDs of active thread characters named in scene/current.md.
# ================================

from __future__ import annotations

from difflib import ndiff
import re

from src.wiki.models import WikiDocument

_COLLECTIVE_PLAYER_MARKERS = ("두 사람", "두사람", "둘이", "둘은", "둘의")
_SHARED_PROPOSAL_MARKERS = ("가자고", "하자고", "제안", "요구", "부탁", "조르", "물었", "묻")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def evidence_is_exact_quote(evidence: str, source_text: str) -> bool:
    """Return whether evidence occurs verbatim in its declared source text."""
    candidate = evidence.strip()
    quote_pairs = (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"), ("`", "`"))
    for opening, closing in quote_pairs:
        if candidate.startswith(opening) and candidate.endswith(closing):
            candidate = candidate[len(opening):-len(closing)].strip()
            break
    return bool(candidate) and candidate in source_text


def first_nonempty_line(text: str) -> str:
    """Return the first nonempty stripped line from text."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def actor_source_player_conflicts(
    original_markdown: str,
    replacement_markdown: str,
    player_reference_tokens: set[str],
) -> list[str]:
    """Return added actor-source lines that establish player or joint action."""
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


def document_body(content: str) -> str:
    """Return the Markdown body with YAML frontmatter removed."""
    return _FRONTMATTER_RE.sub("", content, count=1).strip()


def player_reference_tokens(document: WikiDocument | None) -> set[str]:
    """Return full and Korean-short name tokens from a character document's H1.

    Despite the name (kept for call-site continuity with the player-conflict
    checks above), this also identifies any other character's name — callers
    use it for both the player and third-party owner name gates.
    """
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


def scene_active_profile_ids(documents: list[WikiDocument]) -> set[str]:
    """Return profile IDs of active thread characters named in scene/current.md.

    scene/current.md carries the shared, canonical description of who is
    currently present, but neither its initial `## 시작 기준` prose nor a
    later Updater-produced `## 현재 장면` guarantees one delimited
    "who is present" line — both are freeform Korean narration. Membership is
    therefore judged the same deterministic way `player_reference_tokens`
    already judges a player mention: a character's H1 display name (or its
    Korean two-character short form) must occur as a literal substring of the
    scene document body. This is the sole source of "who is in the current
    scene" for creation-authority and relationship-patch gating; it never
    infers membership from documents other than the current scene document.
    """
    scene_document = next(
        (
            document for document in documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        None,
    )
    if scene_document is None:
        return set()
    scene_body = document_body(scene_document.content)
    active_ids: set[str] = set()
    for document in documents:
        metadata = document.metadata
        if metadata is None or metadata.type != "character" or metadata.profile_id is None:
            continue
        if any(token in scene_body for token in player_reference_tokens(document)):
            active_ids.add(metadata.profile_id)
    return active_ids
