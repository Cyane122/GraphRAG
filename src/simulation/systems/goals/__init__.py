# ================================
# src/simulation/systems/goals/__init__.py
#
# Character life-objective goals for dynamic prompt hints and post-response
# progress updates.
#
# Functions
#   - fetch_goal_hints(owner_id: str, pc_id: str, current_time: datetime, limit: int = 2) -> list[dict] : Fetch active goal hints for the dynamic prompt.
#   - apply_goal_updates(actor_response: str, owner_id: str, pc_id: str, current_time: datetime, event_id: str | None = None) -> None : Update active goals after the actor response.
# ================================

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.config import MODEL_STATE_UPDATER
from src.core.database import async_driver
from src.core.llm.client import extract_json_from_llm, get_model

from src.simulation.systems.goals.models import GoalRecord, GoalStatus, GoalUpdate, VALID_STATUSES, MAX_HINT_LENGTH

async def fetch_goal_hints(
    owner_id: str,
    pc_id: str,
    current_time: datetime,
    limit: int = 2,
) -> list[dict]:
    """
    Return structured dynamic-prompt hints for the owner's active goals.

    The hints are intentionally indirect. Callers can format them into the
    dynamic prompt without exposing hidden intent or forcing completion.
    """
    await _ensure_goal_schema()
    goals = await _fetch_active_goals(owner_id=owner_id, limit=limit)
    return [
        {
            "id": goal["id"],
            "owner_id": owner_id,
            "pc_id": pc_id,
            "title": goal.get("title", "Untitled goal"),
            "hint": _trim_hint(goal.get("next_hint") or goal.get("description") or goal.get("title") or ""),
            "progress": _clamp_int(goal.get("progress"), 0, 100, default=0),
            "subtlety": _clamp_int(goal.get("subtlety"), 0, 10, default=7),
            "current_time": current_time.isoformat(),
        }
        for goal in goals
    ]


async def apply_goal_updates(
    actor_response: str,
    owner_id: str,
    pc_id: str,
    current_time: datetime,
    event_id: str | None = None,
) -> None:
    """
    Analyze an actor response and apply slow progress updates to active goals.

    The function is intentionally fire-and-forget for integration with the
    existing post-actor simulation pipeline.
    """
    await _ensure_goal_schema()
    goals = await _fetch_active_goals(owner_id=owner_id, limit=8)
    if not goals or not actor_response.strip():
        return

    updates = await _generate_goal_update_plan(
        actor_response=actor_response,
        owner_id=owner_id,
        pc_id=pc_id,
        goals=goals,
    )
    if not updates:
        return

    await _apply_goal_update_plan(
        goals=goals,
        updates=updates,
        timestamp=current_time.isoformat(),
        event_id=event_id,
    )


async def _ensure_goal_schema() -> None:
    """Create Goal and PURSUES tables when an older Kuzu DB predates TODO-4."""
    async with async_driver.session() as session:
        for ddl in (
            """CREATE NODE TABLE IF NOT EXISTS Goal(
                id STRING,
                owner_id STRING,
                title STRING,
                description STRING,
                status STRING,
                progress INT64,
                subtlety INT64,
                next_hint STRING,
                trigger_conditions STRING,
                completion_conditions STRING,
                last_progressed_at STRING,
                PRIMARY KEY(id)
            )""",
            "CREATE REL TABLE IF NOT EXISTS PURSUES(FROM Character TO Goal)",
            "CREATE REL TABLE IF NOT EXISTS GOAL_RELATED_EVENT(FROM Goal TO Event)",
        ):
            try:
                await session.run(ddl)
            except Exception as exc:
                print(f"[GoalSystem] schema guard skipped: {exc}")


async def _fetch_active_goals(owner_id: str, limit: int) -> list[GoalRecord]:
    """Load active goals pursued by the given NPC."""
    safe_limit = max(1, min(20, int(limit or 3)))
    async with async_driver.session() as session:
        rec = await session.run(f"""
            MATCH (c:Character {{id: $owner_id}})-[:PURSUES]->(g:Goal)
            WHERE g.status = "active"
            RETURN g.id AS id,
                   g.owner_id AS owner_id,
                   g.title AS title,
                   g.description AS description,
                   g.status AS status,
                   g.progress AS progress,
                   g.subtlety AS subtlety,
                   g.next_hint AS next_hint,
                   g.trigger_conditions AS trigger_conditions,
                   g.completion_conditions AS completion_conditions,
                   g.last_progressed_at AS last_progressed_at
            ORDER BY g.progress DESC, g.id ASC
            LIMIT {safe_limit}
        """, owner_id=owner_id)
        rows = await rec.data()

    return [_normalize_goal_row(row, fallback_owner=owner_id) for row in rows if row.get("id")]


async def _generate_goal_update_plan(
    actor_response: str,
    owner_id: str,
    pc_id: str,
    goals: list[GoalRecord],
) -> list[GoalUpdate]:
    """Ask the lightweight updater model whether goals advanced this turn."""
    context = "\n".join(
        json.dumps(
            {
                "id": goal["id"],
                "title": goal.get("title", ""),
                "description": goal.get("description", ""),
                "progress": goal.get("progress", 0),
                "subtlety": goal.get("subtlety", 7),
                "next_hint": goal.get("next_hint", ""),
                "trigger_conditions": goal.get("trigger_conditions", ""),
                "completion_conditions": goal.get("completion_conditions", ""),
            },
            ensure_ascii=False,
        )
        for goal in goals
    )

    system_prompt = (
        "You are a precise long-term goal tracker for a roleplay simulation. "
        "Return only raw JSON. Never invent goals that are not listed."
    )
    prompt = f"""Track only slow, earned movement on listed life objectives.

Rules:
- Update a goal only if the scene visibly moved it forward, blocked it, or completed it.
- Most ordinary turns should return [].
- Use small progress_delta values: -3..+5 normally, +10 only for a decisive milestone.
- Do not reward mere thoughts unless they become behavior or a concrete decision.
- Keep next_hint private, indirect, and playable in the next scene.
- status must be one of: active, paused, completed, failed, abandoned.

Owner: {owner_id}
PC: {pc_id}

Active goals:
{context}

Actor response:
{actor_response[:2400]}

Return ONLY a JSON array:
[
  {{
    "goal_id": "existing_goal_id",
    "progress_delta": 0,
    "status": "active",
    "next_hint": "private subtle hint for next turn",
    "reason": "brief internal reason"
  }}
]"""

    try:
        model = get_model(MODEL_STATE_UPDATER, system_prompt=system_prompt)
        response = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json",
            },
        )
        parsed = extract_json_from_llm(response.text, source="goal_updates")
    except Exception as exc:
        print(f"[GoalSystem] update plan failed: {exc}")
        return []

    if not isinstance(parsed, list):
        return []
    return [_normalize_goal_update(item) for item in parsed if isinstance(item, dict)]


async def _apply_goal_update_plan(
    goals: list[GoalRecord],
    updates: list[GoalUpdate],
    timestamp: str,
    event_id: str | None,
) -> None:
    """Persist normalized updates and optionally link changed goals to an event."""
    by_id = {goal["id"]: goal for goal in goals}
    updated: list[str] = []
    completed: list[str] = []

    async with async_driver.session() as session:
        for update in updates:
            goal_id = update.get("goal_id", "")
            if goal_id not in by_id:
                continue

            current = by_id[goal_id]
            old_progress = _clamp_int(current.get("progress"), 0, 100, default=0)
            delta = _clamp_int(update.get("progress_delta"), -10, 10, default=0)
            status = update.get("status", "active")
            next_hint = _trim_hint(update.get("next_hint") or current.get("next_hint") or "")

            new_progress = _clamp_int(old_progress + delta, 0, 100, default=old_progress)
            if new_progress >= 100 and status == "active":
                status = "completed"

            if delta == 0 and status == current.get("status", "active") and next_hint == current.get("next_hint", ""):
                continue

            await session.run("""
                MATCH (g:Goal {id: $goal_id})
                SET g.progress = $progress,
                    g.status = $status,
                    g.next_hint = $next_hint,
                    g.last_progressed_at = $timestamp
            """,
                goal_id=goal_id,
                progress=new_progress,
                status=status,
                next_hint=next_hint,
                timestamp=timestamp,
            )
            updated.append(goal_id)
            if status == "completed":
                completed.append(goal_id)
            if event_id:
                await _link_goal_to_event(session, goal_id=goal_id, event_id=event_id)

    if updated:
        print(f"[GoalSystem] updated goals: {updated}")


async def _link_goal_to_event(session: Any, goal_id: str, event_id: str) -> None:
    """Create a GOAL_RELATED_EVENT relation when both nodes exist."""
    try:
        rec = await session.run("""
            MATCH (g:Goal {id: $goal_id})-[:GOAL_RELATED_EVENT]->(e:Event {id: $event_id})
            RETURN g.id AS gid
        """, goal_id=goal_id, event_id=event_id)
        if await rec.single():
            return
        await session.run("""
            MATCH (g:Goal {id: $goal_id}), (e:Event {id: $event_id})
            CREATE (g)-[:GOAL_RELATED_EVENT]->(e)
        """, goal_id=goal_id, event_id=event_id)
    except Exception as exc:
        print(f"[GoalSystem] event link skipped ({goal_id} -> {event_id}): {exc}")


def _normalize_goal_row(row: dict[str, Any], fallback_owner: str) -> GoalRecord:
    """Convert a raw Kuzu row into a typed goal record with safe defaults."""
    return {
        "id": str(row.get("id") or ""),
        "owner_id": str(row.get("owner_id") or fallback_owner),
        "title": str(row.get("title") or "Untitled goal"),
        "description": str(row.get("description") or ""),
        "status": str(row.get("status") or "active"),
        "progress": _clamp_int(row.get("progress"), 0, 100, default=0),
        "subtlety": _clamp_int(row.get("subtlety"), 0, 10, default=7),
        "next_hint": _trim_hint(str(row.get("next_hint") or "")),
        "trigger_conditions": str(row.get("trigger_conditions") or ""),
        "completion_conditions": str(row.get("completion_conditions") or ""),
        "last_progressed_at": str(row.get("last_progressed_at") or ""),
    }


def _normalize_goal_update(raw: dict[str, Any]) -> GoalUpdate:
    """Validate and clamp one LLM-proposed goal update."""
    status = str(raw.get("status") or "active").strip().lower()
    if status not in VALID_STATUSES:
        status = "active"
    return {
        "goal_id": str(raw.get("goal_id") or raw.get("id") or ""),
        "progress_delta": _clamp_int(raw.get("progress_delta"), -10, 10, default=0),
        "status": status,  # type: ignore[typeddict-item]
        "next_hint": _trim_hint(str(raw.get("next_hint") or "")),
        "reason": str(raw.get("reason") or ""),
    }


def _trim_hint(value: str) -> str:
    """Normalize prompt-facing hints to one compact line."""
    text = " ".join(str(value or "").split())
    if len(text) <= MAX_HINT_LENGTH:
        return text
    return text[: MAX_HINT_LENGTH - 3].rstrip() + "..."


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    """Convert a value to int and clamp it into an inclusive range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))
