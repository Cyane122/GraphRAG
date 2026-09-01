# ================================
# src/wiki/commit_header_sync.py
#
# Synchronizes safe accepted Actor header time and location into the current-scene patch.
#
# Functions
#   - synchronize_accepted_header(result: WikiUpdaterResult, documents: list[WikiDocument], user_input: str, actor_response: str) -> None : Merge accepted Actor header time and location into the canonical scene patch.
# ================================

from __future__ import annotations

from datetime import datetime
import re

from src.simulation.prose_headers import (
    parse_prose_header_datetime,
    parse_prose_header_location,
    parse_prose_header_text,
)
from src.wiki.commit_errors import WikiCommitPlanningError
from src.wiki.commit_policy import SCENE_SECTION_ALIASES
from src.wiki.context import scene_datetime_and_location
from src.wiki.markdown import apply_section_patches, parse_markdown_sections
from src.wiki.models import WikiDocument, WikiUpdaterResult
from src.wiki.patches import build_actor_response_section_patch

_TIME_PLACE_HEADING_PATTERN = r"(?:Time and Place|시작 시각과 장소|현재 시각과 장소)"
_EXPLICIT_DATE_JUMP_RE = re.compile(
    r"다음\s*날|내일|모레|며칠\s*후|주일\s*후|주\s*후|달\s*후|"
    r"next\s+day|tomorrow|days?\s+later|weeks?\s+later",
    re.IGNORECASE,
)
_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def _header_location_is_grounded(
    header_location: str,
    current_location: str,
    user_input: str,
) -> bool:
    """Return whether a header location is current or named in player input."""
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
    """Return the canonical English Time and Place bullet."""
    weekday = _WEEKDAY_NAMES[scene_time.weekday()]
    month = _MONTH_NAMES[scene_time.month]
    return (
        f"- It is {scene_time:%H:%M} on {weekday}, {month} "
        f"{scene_time.day}, {scene_time.year}, in {location}."
    )


def _replace_scene_time_place(scene_markdown: str, replacement_line: str) -> str:
    """Replace or add the Time and Place subsection in a complete scene H2."""
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
        raise WikiCommitPlanningError("Current scene replacement has no complete H2 heading")
    return heading.sub(
        lambda match: (
            f"{match.group(1)}\n\n### Time and Place\n\n{replacement_line}"
        ),
        scene_markdown,
        count=1,
    ).rstrip()


def synchronize_accepted_header(
    result: WikiUpdaterResult,
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
) -> None:
    """Merge safe accepted Actor header time and location into a scene patch."""
    header_text = parse_prose_header_text(actor_response)
    header_time = parse_prose_header_datetime(actor_response)
    header_location = parse_prose_header_location(actor_response)
    if not header_text or header_time is None:
        return
    scene_document = next(
        (
            document for document in documents
            if document.metadata is not None and document.metadata.type == "scene"
        ),
        None,
    )
    if scene_document is None:
        raise WikiCommitPlanningError("Updater input has no current scene document")
    current_time, current_location = scene_datetime_and_location(scene_document.content)
    safe_time = current_time
    if header_time >= current_time and (
        header_time.date() == current_time.date() or _EXPLICIT_DATE_JUMP_RE.search(user_input)
    ):
        safe_time = header_time
    safe_location = current_location
    if header_location and _header_location_is_grounded(header_location, current_location, user_input):
        safe_location = header_location
    if safe_time == current_time and safe_location.strip().casefold() == current_location.strip().casefold():
        return

    sections = parse_markdown_sections(scene_document.content)
    aliases = [alias for alias in SCENE_SECTION_ALIASES if (alias,) in sections]
    if len(aliases) != 1:
        raise WikiCommitPlanningError("Current scene must contain exactly one canonical scene H2")
    section_path = (aliases[0],)
    existing_patch = next(
        (patch for patch in result.patches if patch.document == scene_document.path),
        None,
    )
    base_section = sections[section_path]
    target_markdown = existing_patch.replacement_markdown if existing_patch is not None else base_section.markdown
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
        deterministic_patch = build_actor_response_section_patch(
            scene_document,
            section_path,
            replacement,
            header_text,
        )
        if deterministic_patch is None:
            raise WikiCommitPlanningError("Current scene must contain exactly one canonical scene H2")
        apply_section_patches(scene_document, [deterministic_patch])
        result.patches.append(deterministic_patch)
    suffix = "Accepted Actor header time/location synchronized."
    result.summary = f"{result.summary.rstrip()} {suffix}".strip()
