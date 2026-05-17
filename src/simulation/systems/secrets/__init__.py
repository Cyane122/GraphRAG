# ================================
# src/simulation/systems/secrets/__init__.py
#
# Secret hint and reveal lifecycle helpers for the simulation systems package.
# Eligible Secret nodes can contribute subtext-only dynamic prompt hints, and
# reveal conditions can advance Secret state after the actor response.
#
# Classes
#   - SecretHint : Prompt-safe Secret hint data returned to prompt assembly.
#   - SecretRevealUpdate : Secret reveal update data returned after state writes.
#
# Functions
#   - fetch_secret_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int) -> list[dict] : Fetch prompt-safe hints for eligible secrets.
#   - apply_secret_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None) -> None : Apply reveal state updates after actor generation.
# ================================

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from src.core.database import async_driver
from src.simulation.events.evaluator import evaluate_conditions

_REVEALED_STATUSES = {"revealed", "done"}
_DEFAULT_COOLDOWN_HOURS = 24


from src.simulation.systems.secrets.models import SecretHint, SecretRevealUpdate

async def fetch_secret_hints(
    owner_id: str,
    pc_id: str,
    current_time: datetime,
    limit: int = 2,
) -> list[dict]:
    """
    Fetch prompt-safe Secret hints for dynamic prompt assembly.

    The returned hint uses only public_hint, never private_summary. owner_id is the
    Secret owner currently in focus; pc_id is accepted for the integration contract
    and future relationship-scoped filtering.
    """
    await _ensure_secret_schema()
    rows = await _fetch_secret_rows()
    eligible: list[SecretHint] = []

    for row in rows:
        if not _matches_context(row, owner_id, pc_id):
            continue
        if _is_revealed(row):
            continue
        if _is_on_cooldown(row.get("last_hinted_at"), current_time, _DEFAULT_COOLDOWN_HOURS):
            continue
        if not await _conditions_met(row.get("reveal_conditions"), current_time):
            continue

        hint = _to_secret_hint(row)
        if hint["hint"]:
            eligible.append(hint)

    eligible.sort(key=lambda h: (-h["sensitivity"], h["reveal_level"], h["title"]))
    selected = eligible[:max(0, limit)]

    if selected:
        await _mark_hinted([hint["id"] for hint in selected], current_time)

    return [dict(hint) for hint in selected]


def _build_secret_hint_block(hints: list[SecretHint]) -> str:
    """
    Format Secret hints as hidden dynamic prompt comments.

    The block is intentionally subtext-oriented: it tells the actor what pressure
    can leak into behavior without exposing private_summary or demanding a reveal.
    """
    if not hints:
        return ""

    lines = [
        "<!-- Secret/subtext hints: keep these implicit; do not state the secret directly. -->"
    ]
    for hint in hints:
        lines.append(f"<!-- [{hint['title']}] {hint['hint']} -->")
    return "\n".join(lines)


def _matches_context(row: dict, owner_id: str, pc_id: str) -> bool:
    """
    Return true when a Secret belongs to the current focus owner.

    pc_id is intentionally accepted even though the current schema has no direct
    PC visibility column. Keeping it in this boundary avoids another API change
    when relationship-scoped visibility is added.
    """
    _ = pc_id
    return not owner_id or row.get("owner_id") == owner_id


async def apply_secret_updates(
    actor_response: str,
    owner_id: str,
    pc_id: str,
    current_time: datetime,
    event_id: str | None = None,
) -> None:
    """
    Apply reveal updates for Secrets after the actor response has been generated.

    A Secret is revealed when its reveal_conditions evaluate true. The actor
    response is used only as a soft audit signal for logs; conditions remain the
    source of truth so state updates stay deterministic.
    """
    await _ensure_secret_schema()
    rows = await _fetch_secret_rows()

    for row in rows:
        if not _matches_context(row, owner_id, pc_id):
            continue
        if _is_revealed(row):
            continue
        if not await _conditions_met(row.get("reveal_conditions"), current_time):
            continue

        previous_level = _as_int(row.get("current_reveal_level"), 0)
        update: SecretRevealUpdate = {
            "id": str(row.get("id") or ""),
            "title": str(row.get("title") or ""),
            "owner_id": str(row.get("owner_id") or ""),
            "previous_status": str(row.get("status") or ""),
            "new_status": "revealed",
            "previous_reveal_level": previous_level,
            "new_reveal_level": previous_level + 1,
            "matched_response": _response_mentions_secret(actor_response, row),
        }

        await _apply_reveal_update(update, current_time)
        if event_id:
            await _link_secret_to_event(update["id"], event_id)
        print(
            "[Secret] reveal "
            f"{update['id']} owner={owner_id} "
            f"matched_response={update.get('matched_response', False)}"
        )


async def _fetch_secret_rows(secret_ids: list[str] | None = None) -> list[dict]:
    """Fetch Secret rows and optionally filter by id in Python for Kuzu compatibility."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (s:Secret)
            RETURN s.id                   AS id,
                   s.owner_id             AS owner_id,
                   s.title                AS title,
                   s.private_summary      AS private_summary,
                   s.public_hint          AS public_hint,
                   s.status               AS status,
                   s.sensitivity          AS sensitivity,
                   s.reveal_conditions    AS reveal_conditions,
                   s.current_reveal_level AS current_reveal_level,
                   s.last_hinted_at       AS last_hinted_at
        """)
        rows = await rec.data()

    if secret_ids is None:
        return rows

    allowed = set(secret_ids)
    return [row for row in rows if row.get("id") in allowed]


async def _ensure_secret_schema() -> None:
    """Create Secret tables when an older Kuzu DB predates TODO-4."""
    async with async_driver.session() as session:
        for ddl in (
            """CREATE NODE TABLE IF NOT EXISTS Secret(
                id STRING,
                owner_id STRING,
                title STRING,
                private_summary STRING,
                public_hint STRING,
                status STRING,
                sensitivity INT64,
                reveal_conditions STRING,
                current_reveal_level INT64,
                last_hinted_at STRING,
                PRIMARY KEY(id)
            )""",
            "CREATE REL TABLE IF NOT EXISTS HAS_SECRET(FROM Character TO Secret)",
            "CREATE REL TABLE IF NOT EXISTS ROOTED_IN(FROM Secret TO Event)",
            "CREATE REL TABLE IF NOT EXISTS TRIGGERED_BY(FROM Secret TO Item)",
        ):
            try:
                await session.run(ddl)
            except Exception as exc:
                print(f"[Secret] schema guard skipped: {exc}")


def _is_revealed(row: dict) -> bool:
    """Return true when a Secret should no longer produce hints or reveal writes."""
    return str(row.get("status") or "").lower() in _REVEALED_STATUSES


def _is_on_cooldown(
    last_hinted_at: str | None,
    current_dt: datetime,
    cooldown_hours: int,
) -> bool:
    """Return true when the previous hint is still within the cooldown window."""
    if cooldown_hours <= 0 or not last_hinted_at:
        return False

    last_dt = _parse_dt(last_hinted_at)
    if last_dt is None:
        return False

    return current_dt - last_dt < timedelta(hours=cooldown_hours)


async def _conditions_met(raw_conditions: object, current_dt: datetime) -> bool:
    """Normalize stored JSON conditions and evaluate them with the shared evaluator."""
    conditions = _parse_conditions(raw_conditions)
    if not conditions:
        return False
    return await evaluate_conditions(conditions, current_dt)


def _parse_conditions(raw_conditions: object) -> list[dict]:
    """Parse a Secret condition payload into the list shape expected by evaluator."""
    if not raw_conditions:
        return []

    if isinstance(raw_conditions, list):
        return [item for item in raw_conditions if isinstance(item, dict)]
    if isinstance(raw_conditions, dict):
        return [raw_conditions]
    if not isinstance(raw_conditions, str):
        return []

    try:
        parsed = json.loads(raw_conditions)
    except (json.JSONDecodeError, TypeError):
        return []

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _to_secret_hint(row: dict) -> SecretHint:
    """Convert a database row to the public prompt hint shape."""
    return {
        "id": str(row.get("id") or ""),
        "owner_id": str(row.get("owner_id") or ""),
        "title": str(row.get("title") or ""),
        "hint": str(row.get("public_hint") or ""),
        "status": str(row.get("status") or ""),
        "sensitivity": _as_int(row.get("sensitivity"), 0),
        "reveal_level": _as_int(row.get("current_reveal_level"), 0),
    }


async def _mark_hinted(secret_ids: list[str], current_dt: datetime) -> None:
    """Persist last_hinted_at for all hints returned to the prompt layer."""
    hinted_at = current_dt.isoformat()

    async with async_driver.session() as session:
        for secret_id in secret_ids:
            await session.run(
                "MATCH (s:Secret {id: $id}) SET s.last_hinted_at = $hinted_at",
                id=secret_id,
                hinted_at=hinted_at,
            )


async def _apply_reveal_update(
    update: SecretRevealUpdate,
    current_dt: datetime,
) -> None:
    """Write the reveal status and level for one Secret node."""
    async with async_driver.session() as session:
        await session.run(
            """
            MATCH (s:Secret {id: $id})
            SET s.status = $status,
                s.current_reveal_level = $level,
                s.last_hinted_at = $updated_at
            """,
            id=update["id"],
            status=update["new_status"],
            level=update["new_reveal_level"],
            updated_at=current_dt.isoformat(),
        )


async def _link_secret_to_event(secret_id: str, event_id: str) -> None:
    """Link a revealed Secret to the turn Event when both nodes exist."""
    async with async_driver.session() as session:
        await session.run(
            """
            MATCH (s:Secret {id: $secret_id}), (e:Event {id: $event_id})
            CREATE (s)-[:ROOTED_IN]->(e)
            """,
            secret_id=secret_id,
            event_id=event_id,
        )


def _response_mentions_secret(actor_response: str, row: dict) -> bool:
    """
    Return a soft signal for whether the actor response appears to surface a Secret.

    This does not gate reveal writes. It helps callers inspect whether a reveal was
    also visible in prose instead of only condition-driven.
    """
    if not actor_response:
        return False

    haystack = _normalize_text(actor_response)
    needles = [
        str(row.get("title") or ""),
        str(row.get("public_hint") or ""),
    ]

    private_summary = str(row.get("private_summary") or "")
    needles.extend(_long_terms(private_summary))

    return any(_normalize_text(needle) in haystack for needle in needles if needle)


def _long_terms(text: str) -> list[str]:
    """Extract high-signal text fragments from private summaries for audit matching."""
    terms = re.findall(r"\w{4,}", text)
    return terms[:5]


def _normalize_text(text: str) -> str:
    """Normalize text for loose response matching."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_dt(value: str) -> datetime | None:
    """Parse an ISO datetime string and return None on invalid values."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _as_int(value: object, default: int) -> int:
    """Coerce Kuzu numeric values to int with a conservative default."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
