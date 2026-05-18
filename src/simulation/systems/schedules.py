# ================================
# src/simulation/systems/schedules.py
#
# Fetches recurring character Schedule nodes as time-aware prompt hints.
#
# Functions
#   - fetch_schedule_context(current_time: datetime, window_minutes: int = 120) -> dict : Fetch routine and same-day schedule hints for all active characters
#   - fetch_schedule_hints(current_time: datetime, window_minutes: int = 120) -> list[dict] : Fetch same-day schedule hints for all active characters
# ================================

import json
from datetime import datetime

from src.core.database import async_driver


async def fetch_schedule_context(
    current_time: datetime,
    window_minutes: int = 120,
) -> dict:
    """Fetch always-on routine schedule summaries and same-day detailed hints for all active characters."""
    rows = await _fetch_schedule_rows()
    routine_schedules = [_routine_hint(row, current_time) for row in rows]
    detailed_schedules = [
        hint
        for row in rows
        if (hint := _schedule_hint_for_time(row, current_time, window_minutes))
    ]
    detailed_schedules = sorted(
        detailed_schedules,
        key=lambda item: (
            0 if item.get("timing") == "active" else 1,
            int(item.get("minutes_until") or 0),
            -int(item.get("prompt_priority") or 0),
        ),
    )
    return {
        "routine_schedules": [hint for hint in routine_schedules if hint],
        "schedules": detailed_schedules,
    }


async def fetch_schedule_hints(
    current_time: datetime,
    window_minutes: int = 120,
) -> list[dict]:
    """Fetch same-day Schedule nodes with detailed hints."""
    context = await fetch_schedule_context(current_time, window_minutes)
    return context["schedules"]


async def _fetch_schedule_rows() -> list[dict]:
    """Load all active Schedule rows."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character)-[:HAS_SCHEDULE]->(s:Schedule)
            WHERE s.status = 'active' OR s.status = '' OR s.status IS NULL
            OPTIONAL MATCH (s)-[:SCHEDULED_AT]->(l:Location)
            RETURN c.id AS owner_id,
                   c.name AS owner_name,
                   s.id AS id,
                   s.name AS name,
                   s.activity AS activity,
                   s.summary AS summary,
                   s.prompt_hint AS prompt_hint,
                   s.prompt_priority AS prompt_priority,
                   s.material AS material,
                   s.recurrence AS recurrence,
                   s.day_of_week AS day_of_week,
                   s.day_of_weeks AS day_of_weeks,
                   s.date AS date,
                   s.start_time AS start_time,
                   s.end_time AS end_time,
                   s.start_minute AS start_minute,
                   s.end_minute AS end_minute,
                   s.location_id AS location_id,
                   l.name AS location_name,
                   s.tags AS tags
            """
        )
        rows = await result.data()

    return [dict(row) for row in rows]


def _schedule_hint_for_time(row: dict, current_time: datetime, window_minutes: int) -> dict | None:
    """Return a detailed prompt hint when a schedule applies to the current date."""
    if not _matches_date(row, current_time):
        return None

    start_minute = _coerce_minute(row.get("start_minute"), row.get("start_time"))
    end_minute = _coerce_minute(row.get("end_minute"), row.get("end_time"))
    current_minute = current_time.hour * 60 + current_time.minute

    timing = "today"
    minutes_until = None
    if start_minute >= 0:
        if end_minute >= 0 and _is_active_window(current_minute, start_minute, end_minute):
            timing = "active"
            minutes_until = 0
        else:
            delta = start_minute - current_minute
            if delta < 0:
                timing = "past_today"
            elif delta > window_minutes:
                timing = "today"
                minutes_until = delta
            else:
                timing = "upcoming"
                minutes_until = delta

    material = row.get("material") or ""
    realism = _schedule_realism_fields(material)
    return {
        "owner_id": row.get("owner_id"),
        "owner_name": row.get("owner_name") or row.get("owner_id"),
        "id": row.get("id"),
        "name": row.get("name") or row.get("activity") or row.get("id"),
        "activity": row.get("activity") or "",
        "summary": row.get("summary") or "",
        "prompt_hint": row.get("prompt_hint") or "",
        "prompt_priority": row.get("prompt_priority") or 0,
        "material": material,
        **realism,
        "timing": timing,
        "minutes_until": minutes_until,
        "start_time": row.get("start_time") or _format_minute(start_minute),
        "end_time": row.get("end_time") or _format_minute(end_minute),
        "location_id": row.get("location_id") or "",
        "location_name": row.get("location_name") or "",
        "tags": row.get("tags") or [],
    }


def _routine_hint(row: dict, current_time: datetime) -> dict:
    """Return minimal always-on routine info: who, when, where, and what."""
    start_minute = _coerce_minute(row.get("start_minute"), row.get("start_time"))
    end_minute = _coerce_minute(row.get("end_minute"), row.get("end_time"))
    material = row.get("material") or ""
    realism = _schedule_realism_fields(material)
    return {
        "owner_id": row.get("owner_id"),
        "owner_name": row.get("owner_name") or row.get("owner_id"),
        "id": row.get("id"),
        "name": row.get("name") or row.get("activity") or row.get("id"),
        "activity": row.get("activity") or "",
        "recurrence": row.get("recurrence") or "weekly",
        "day_of_week": row.get("day_of_week"),
        "day_of_weeks": sorted(_normalize_weekdays(row.get("day_of_weeks"))),
        "date": row.get("date") or "",
        "start_time": row.get("start_time") or _format_minute(start_minute),
        "end_time": row.get("end_time") or _format_minute(end_minute),
        "location_id": row.get("location_id") or "",
        "location_name": row.get("location_name") or "",
        **realism,
        "is_today": _matches_date(row, current_time),
    }


def _schedule_realism_fields(raw_material: object) -> dict:
    """Extract optional time-realism hints from Schedule.material."""
    material = _parse_material(raw_material)
    if not material:
        return {}

    return {
        key: value
        for key in (
            "preparation_time_min",
            "travel_time_min",
            "flexibility",
            "lateness_tolerance",
            "can_skip",
            "requires_transition_scene",
        )
        if (value := material.get(key)) not in (None, "", [])
    }


def _parse_material(raw_material: object) -> dict:
    """Parse Schedule.material into a dict when it stores structured hints."""
    if isinstance(raw_material, dict):
        return raw_material
    if not isinstance(raw_material, str) or not raw_material.strip():
        return {}
    try:
        parsed = json.loads(raw_material)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _matches_date(row: dict, current_time: datetime) -> bool:
    """Check recurrence/date fields against the current in-game date."""
    recurrence = (row.get("recurrence") or "weekly").lower()
    schedule_date = row.get("date") or ""
    if recurrence == "daily":
        return True
    if recurrence == "once":
        return schedule_date == current_time.date().isoformat()
    day_of_weeks = _normalize_weekdays(row.get("day_of_weeks"))
    if day_of_weeks:
        return current_time.weekday() in day_of_weeks
    day_of_week = row.get("day_of_week")
    if day_of_week in (None, "", -1):
        return True
    try:
        return int(day_of_week) == current_time.weekday()
    except (TypeError, ValueError):
        return True


def _normalize_weekdays(raw: object) -> set[int]:
    """Normalize list-like weekday values from Kuzu into Python weekday numbers."""
    if raw in (None, "", -1):
        return set()
    if isinstance(raw, int):
        return {raw} if 0 <= raw <= 6 else set()
    if isinstance(raw, (list, tuple, set)):
        days: set[int] = set()
        for value in raw:
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6:
                days.add(day)
        return days
    try:
        day = int(raw)
    except (TypeError, ValueError):
        return set()
    return {day} if 0 <= day <= 6 else set()


def _coerce_minute(raw_minute: object, raw_time: object) -> int:
    """Normalize minute-of-day fields, accepting HH:MM fallback strings."""
    try:
        minute = int(raw_minute)
    except (TypeError, ValueError):
        minute = -1
    if minute >= 0:
        return minute
    return _parse_hhmm(str(raw_time or ""))


def _parse_hhmm(value: str) -> int:
    """Parse HH:MM into minutes since midnight."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        return -1
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return -1
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return -1
    return hour * 60 + minute


def _is_active_window(current_minute: int, start_minute: int, end_minute: int) -> bool:
    """Check inclusive schedule windows, including overnight ranges."""
    if end_minute < start_minute:
        return current_minute >= start_minute or current_minute <= end_minute
    return start_minute <= current_minute <= end_minute


def _format_minute(minute: int) -> str:
    """Format minutes since midnight as HH:MM."""
    if minute < 0:
        return ""
    hour, remainder = divmod(minute, 60)
    return f"{hour:02d}:{remainder:02d}"
