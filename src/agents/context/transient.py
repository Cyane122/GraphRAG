# ================================
# src/agents/context/transient.py
#
# Helpers for removing stale transient facts from prompt context.
#
# Functions
#   - sanitize_location_hints_for_turn(location_nodes: list[dict], user_input: str, recent_story: str = "") -> list[dict] : Drop location hints contradicted by the current turn
# ================================

import re


_ABSENCE_WORD_RE = re.compile(
    r"(집에\s*없|없다|없고|안\s*계시|안\s*계신|나갔|나감|떠났|외박|자고\s*올|"
    r"학원\s*갔|약속.*(?:자고|외박|나갔|나감))"
)

_SUBJECT_TERMS: dict[str, tuple[str, ...]] = {
    "sibling": ("동생", "남동생", "여동생"),
    "parents": ("부모님", "엄마", "아빠", "어머니", "아버지"),
}


def sanitize_location_hints_for_turn(
    location_nodes: list[dict],
    user_input: str,
    recent_story: str = "",
) -> list[dict]:
    """Drop transient occupant hints when the current turn says that person is absent."""
    absent_subjects = _detect_absent_subjects(f"{recent_story}\n{user_input}")
    if not absent_subjects:
        return location_nodes

    sanitized = []
    for node in location_nodes:
        updated = dict(node)
        for key in ("prompt_hint", "description", "summary"):
            value = updated.get(key)
            if isinstance(value, str):
                updated[key] = _remove_contradicted_sentences(value, absent_subjects)
        sanitized.append(updated)
    return sanitized


def _detect_absent_subjects(text: str) -> set[str]:
    """Return subject groups explicitly marked as absent in the current user input."""
    absent: set[str] = set()
    for clause in re.split(r"[\n。.!?*]+", text or ""):
        if not _ABSENCE_WORD_RE.search(clause):
            continue
        for subject, terms in _SUBJECT_TERMS.items():
            if any(term in clause for term in terms):
                absent.add(subject)
    return absent


def _remove_contradicted_sentences(text: str, absent_subjects: set[str]) -> str:
    """Remove sentence fragments that assert an absent subject is currently present."""
    if not text:
        return text

    separators = re.compile(r"([,.。.!?]|,|\n)")
    parts = separators.split(text)
    kept: list[str] = []
    for idx in range(0, len(parts), 2):
        fragment = parts[idx]
        separator = parts[idx + 1] if idx + 1 < len(parts) else ""
        if _is_presence_fragment(fragment, absent_subjects):
            continue
        kept.append(fragment + separator)

    cleaned = "".join(kept).strip(" ,.\n")
    return cleaned


def _is_presence_fragment(fragment: str, absent_subjects: set[str]) -> bool:
    """Return True when a fragment says an explicitly absent subject is present."""
    if not fragment:
        return False
    presence_words = ("있다", "있음", "있고", "거실에", "방에", "집에")
    if not any(word in fragment for word in presence_words):
        return False

    for subject in absent_subjects:
        terms = _SUBJECT_TERMS.get(subject, ())
        if any(term in fragment for term in terms):
            return True
    return False
