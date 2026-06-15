# ================================
# src/simulation/systems/time_rules.py
#
# Fetches active Rule nodes that describe world or location time constraints.
#
# Functions
#   - fetch_time_rule_context(current_time: datetime, location_id: str | None = None, limit: int = 6) -> dict : Fetch prompt-ready time rules
#   - _fetch_location_scope_ids(session, location_id: str) -> list[str] : Return location plus ancestors for scoped rules
# ================================

from datetime import datetime

from src.core.database import async_driver

_TIME_RULE_TAGS = {"time", "time_rule", "routine_rule", "institution_rule", "schedule_rule"}
_TIME_RULE_KEYWORDS = (
    "time", "hour", "schedule", "routine", "school", "class", "work", "curfew",
    "시간", "일정", "학교", "수업", "하교", "종례", "점심", "통금", "영업", "운영",
)


async def fetch_time_rule_context(
    current_time: datetime,
    location_id: str | None = None,
    limit: int = 6,
) -> dict:
    """Fetch active time-related Rule nodes for Manager planning and prompt rendering."""
    rows = await _fetch_rule_rows(location_id or "", limit * 3)
    rules = [_time_rule_hint(row, current_time) for row in rows if _is_time_rule(row)]
    return {"time_rules": rules[:limit]} if rules else {}


async def _fetch_rule_rows(location_id: str, limit: int) -> list[dict]:
    """Load active global or location-matching Rule rows."""
    safe_limit = max(1, int(limit))
    async with async_driver.session() as session:
        location_scope_ids = await _fetch_location_scope_ids(session, location_id)
        result = await session.run(
            f"""
            MATCH (r:Rule)
            WHERE (r.status = '' OR r.status = 'active' OR r.status IS NULL)
              AND (
                  r.location_id = '' OR r.location_id IS NULL
                  OR r.location_id IN $location_scope_ids
              )
            RETURN r.id AS id,
                   r.name AS name,
                   r.summary AS summary,
                   r.prompt_hint AS prompt_hint,
                   r.prompt_priority AS prompt_priority,
                   r.tags AS tags,
                   r.location_id AS location_id,
                   r.owner_id AS owner_id,
                   r.scene_type AS scene_type
            ORDER BY r.prompt_priority DESC
            LIMIT {safe_limit}
            """,
            location_scope_ids=location_scope_ids,
        )
        rows = await result.data()

    return [dict(row) for row in rows]


async def _fetch_location_scope_ids(session, location_id: str) -> list[str]:
    """Return the current location and ancestors so parent-scoped rules still apply."""
    if not location_id:
        return []

    scope_ids: list[str] = []
    current_id = location_id
    for _ in range(5):
        if not current_id or current_id in scope_ids:
            break
        scope_ids.append(current_id)
        try:
            result = await session.run(
                """
                MATCH (l:Location {id: $location_id})-[:PART_OF]->(p:Location)
                RETURN p.id AS parent_id
                LIMIT 1
                """,
                location_id=current_id,
            )
            record = await result.single()
        except Exception:
            break
        current_id = str(record.get("parent_id") or "") if record else ""
    return scope_ids


def _is_time_rule(row: dict) -> bool:
    """Return True when a Rule is explicitly tagged or named as time-related."""
    tags = {str(tag).lower() for tag in row.get("tags") or []}
    if tags & _TIME_RULE_TAGS:
        return True

    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("id", "name", "summary", "prompt_hint")
    )
    return any(keyword in text for keyword in _TIME_RULE_KEYWORDS)


def _time_rule_hint(row: dict, current_time: datetime) -> dict:
    """Normalize a Rule row into the shape consumed by classifiers and renderers."""
    return {
        key: value
        for key, value in {
            "id": row.get("id"),
            "name": row.get("name") or row.get("id"),
            "summary": row.get("summary") or "",
            "prompt_hint": row.get("prompt_hint") or "",
            "prompt_priority": row.get("prompt_priority") or 0,
            "tags": row.get("tags") or [],
            "location_id": row.get("location_id") or "",
            "owner_id": row.get("owner_id") or "",
            "scene_type": row.get("scene_type") or "",
            "current_time": current_time.strftime("%Y-%m-%d %H:%M"),
        }.items()
        if value not in (None, "", [])
    }
