# ================================
# src/simulation/prose_headers.py
#
# Parse accepted-response prose headers without importing DB-backed state modules.
#
# Functions
#   - parse_prose_header_text(actor_response: str) -> str | None : Return the first accepted prose header text.
#   - parse_prose_header_datetime(actor_response: str) -> datetime | None : Parse the accepted Actor header time.
#   - parse_prose_header_location(actor_response: str) -> str | None : Parse the accepted Actor header location.
# ================================

from __future__ import annotations

import re
from datetime import datetime

_PROSE_HEADER_LOCATION_RE = re.compile(r"\*\*[^*\n]*?\d{1,2}\s*분\s*,\s*([^*\n]+?)\s*\*\*")
_ANALYZE_BLOCK_RE = re.compile(r"<analyze>[\s\S]*?</analyze>", re.IGNORECASE)
_BOLD_HEADER_RE = re.compile(r"\*\*(?P<header>[^*\n]+)\*\*")
_HEADER_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*년\s*"
    r"(?P<month>\d{1,2})\s*월\s*"
    r"(?P<day>\d{1,2})\s*일"
    r"(?:\s*,?\s*\(?[월화수목금토일]\s*요일?\)?)?"
)
_HEADER_TIME_RE = re.compile(
    r"(?:(?P<ampm>오전|오후|새벽|아침|저녁|밤)\s*)?"
    r"(?P<hour>\d{1,2})\s*시"
    r"(?:\s*(?P<minute>\d{1,2})\s*분)?"
)


def _visible_prose(actor_response: str) -> str:
    """Return response text with analysis blocks removed for prose-header parsing."""
    return _ANALYZE_BLOCK_RE.sub("", actor_response or "").strip()


def parse_prose_header_text(actor_response: str) -> str | None:
    """Return the first bold prose header outside analyze blocks."""
    match = _BOLD_HEADER_RE.search(_visible_prose(actor_response))
    return match.group("header").strip() if match else None


def _coerce_header_hour(hour: int, ampm: str | None) -> int:
    """Convert Korean AM/PM markers into a 24-hour clock."""
    marker = str(ampm or "").strip()
    if marker in {"오후", "저녁", "밤"} and hour < 12:
        return hour + 12
    if marker in {"오전", "새벽", "아침"} and hour == 12:
        return 0
    return hour


def parse_prose_header_datetime(actor_response: str) -> datetime | None:
    """Parse the accepted Actor prose header into a datetime, if present."""
    header = parse_prose_header_text(actor_response)
    if not header:
        return None
    date_match = _HEADER_DATE_RE.search(header)
    time_match = _HEADER_TIME_RE.search(header)
    if not date_match or not time_match:
        return None
    try:
        hour = _coerce_header_hour(int(time_match.group("hour")), time_match.group("ampm"))
        minute = int(time_match.group("minute") or 0)
        return datetime(
            int(date_match.group("year")),
            int(date_match.group("month")),
            int(date_match.group("day")),
            hour,
            minute,
        )
    except ValueError:
        return None


def parse_prose_header_location(actor_response: str) -> str | None:
    """Extract the prose-header location from the accepted Actor response."""
    header = parse_prose_header_text(actor_response)
    if header:
        date_match = _HEADER_DATE_RE.search(header)
        if date_match:
            suffix = header[date_match.end():].strip()
            time_match = _HEADER_TIME_RE.search(suffix)
            if time_match:
                suffix = suffix[time_match.end():].strip()
            suffix = re.sub(r"^\s*[,，.。]?\s*\(?[월화수목금토일]\s*요일?\)?\s*", "", suffix)
            suffix = suffix.lstrip(" ,，.。")
            if suffix:
                location = re.split(r"[,，.。]\s*", suffix)[-1].strip()
                if location:
                    return location

    match = _PROSE_HEADER_LOCATION_RE.search(_visible_prose(actor_response))
    return match.group(1).strip() if match else None
