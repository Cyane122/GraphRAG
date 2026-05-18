# ================================
# src/simulation/state/relationships.py
#
# Detect and persist relationship changes among secondary scene characters.
#
# Functions
#   - apply_scene_relationship_updates(actor_response: str, participant_ids: list[str], primary_pair: tuple[str, str] | None = None) -> list[dict[str, Any]] : Update RELATIONSHIP edges touched by a scene.
#   - _bounded_score_update(current_value: object, requested_value: int | None, field: str, actor_response: str) -> int | None : Limit secondary relationship score jumps.
# ================================

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from src.config import MODEL_PRO_UPDATER as PRO_MODEL
from src.core.database import async_driver, update_relationship_fields


_AFFINITY_MEANINGFUL_STEP_CAP = 5
_AFFINITY_MILESTONE_STEP_CAP = 10
_TRUST_MEANINGFUL_STEP_CAP = 3
_TRUST_MILESTONE_STEP_CAP = 6
_RELATIONSHIP_MILESTONE_RE = re.compile(
    r"(confess|confession|reconcile|reconciliation|breakup|betray|saved|rescue|"
    r"first intimacy|near-death|life-saving|고백|화해|이별|배신|구해|구했다|구해줬|"
    r"목숨|첫 관계|처음으로|결정적|돌이킬 수)",
    re.IGNORECASE,
)


class RelationshipTarget(BaseModel):
    """A directed relationship edge that can be updated."""

    source_id: str
    target_id: str
    source_name: str | None = None
    target_name: str | None = None
    current: dict[str, Any] = Field(default_factory=dict)


class RelationshipUpdate(BaseModel):
    """A conservative update for one directed relationship edge."""

    source_id: str
    target_id: str
    affinity: int | None = None
    trust: int | None = None
    rel_type: str | None = None
    current_status: str | None = None
    summary: str | None = None


class RelationshipUpdatePlan(BaseModel):
    """Relationship updates extracted from one accepted scene."""

    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list)


def _unique_ordered(values: list[str]) -> list[str]:
    """Return non-empty ids in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_primary_pair(source_id: str, target_id: str, primary_pair: tuple[str, str] | None) -> bool:
    """Return whether the edge belongs to the already-handled main relationship."""
    if not primary_pair:
        return False
    return {source_id, target_id} == set(primary_pair)


async def _fetch_relationship_targets(
    participant_ids: list[str],
    primary_pair: tuple[str, str] | None,
) -> list[RelationshipTarget]:
    """Fetch directed relationship edges between current scene participants."""
    participants = _unique_ordered(participant_ids)
    if len(participants) < 2:
        return []

    targets: list[RelationshipTarget] = []
    async with async_driver.session() as session:
        for source_id in participants:
            for target_id in participants:
                if source_id == target_id or _is_primary_pair(source_id, target_id, primary_pair):
                    continue
                rec = await session.run(
                    """
                    MATCH (a:Character {id: $source_id})-[r:RELATIONSHIP]->(b:Character {id: $target_id})
                    RETURN a.name AS source_name,
                           b.name AS target_name,
                           r.type AS rel_type,
                           r.affinity AS affinity,
                           r.trust AS trust,
                           r.current_status AS current_status,
                           r.summary AS summary
                    """,
                    source_id=source_id,
                    target_id=target_id,
                )
                row = await rec.single()
                if not row:
                    continue
                targets.append(
                    RelationshipTarget(
                        source_id=source_id,
                        target_id=target_id,
                        source_name=row["source_name"],
                        target_name=row["target_name"],
                        current={
                            "type": row["rel_type"],
                            "affinity": row["affinity"],
                            "trust": row["trust"],
                            "current_status": row["current_status"],
                            "summary": row["summary"],
                        },
                    )
                )
    return targets


def _target_lines(targets: list[RelationshipTarget]) -> str:
    """Render relationship targets for the Pro updater prompt."""
    lines = []
    for target in targets:
        lines.append(
            f"- {target.source_id}->{target.target_id} "
            f"({target.source_name or target.source_id} to {target.target_name or target.target_id}): "
            f"{json.dumps(target.current, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def _parse_relationship_plan(raw_plan: object) -> RelationshipUpdatePlan:
    """Validate LLM JSON with a Pydantic boundary model."""
    if not isinstance(raw_plan, dict):
        return RelationshipUpdatePlan()
    try:
        return RelationshipUpdatePlan.model_validate(raw_plan)
    except ValueError:
        return RelationshipUpdatePlan()


async def _run_relationship_update(
    actor_response: str,
    targets: list[RelationshipTarget],
) -> RelationshipUpdatePlan:
    """Ask the Pro model for relationship changes among scene participants."""
    from src.core.llm.client import extract_json_from_llm, get_model

    if not targets:
        return RelationshipUpdatePlan()

    system_instruction = (
        "You are a conservative Pro relationship updater for a graph-based Korean roleplay system. "
        "Update only relationships directly evidenced by the accepted scene."
    )
    prompt = f"""## Directed relationship targets
{_target_lines(targets)}

## Task
Extract relationship changes between the listed scene participants.
Use only real source_id and target_id values from the target list.

Allowed updates:
- affinity: integer -100..100 absolute score after this scene, not a delta.
- trust: integer -100..100 absolute score after this scene, not a delta.
- rel_type: acquaintance / stranger / classmate / coworker / friend / rival / family / lover / ex / customer / mentor.
- current_status: one concise sentence describing the current relation after the scene.
- summary: 1-2 concise sentences only for durable relationship changes.

Rules:
- Omit unchanged edges.
- Do not update the main PC/NPC pair here.
- Do not infer hidden intimacy, friendship, hostility, or trust without explicit scene evidence.
- Keep score movement small: affinity normally changes by at most 5, trust by at most 3.
- Larger changes require rare milestones such as confession, betrayal, rescue, decisive reconciliation, near-breakup, or first intimacy.
- Trust should grow slower than affinity and should not rise from embarrassment, attraction, politeness, or passive compliance alone.
- First meetings may create low but nonzero awareness/trust if they directly interacted.
- If A's view of B changes differently than B's view of A, return both directed edges.

Return ONLY valid JSON:
{{
  "relationship_updates": [
    {{
      "source_id": "<id>",
      "target_id": "<id>",
      "affinity": 0,
      "trust": 10,
      "rel_type": "acquaintance",
      "current_status": "They have just met and remain cautious.",
      "summary": "optional"
    }}
  ]
}}

Scene:
{actor_response[:3500]}"""

    model = get_model(model_name=PRO_MODEL, system_prompt=system_instruction)
    try:
        response = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 2048,
                "response_mime_type": "application/json",
            },
        )
    except TimeoutError:
        print("[RelationshipUpdater] timeout")
        return RelationshipUpdatePlan()

    raw_plan = extract_json_from_llm(response.text, source="relationship_updater")
    return _parse_relationship_plan(raw_plan)


def _as_int(value: object) -> int | None:
    """Coerce a relationship score to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_score_update(
    current_value: object,
    requested_value: int | None,
    field: str,
    actor_response: str,
) -> int | None:
    """Limit secondary relationship score jumps before writing absolute values."""
    if requested_value is None:
        return None

    requested = max(-100, min(100, int(requested_value)))
    current = _as_int(current_value)
    if current is None:
        return requested

    if requested == current:
        return None

    milestone = bool(_RELATIONSHIP_MILESTONE_RE.search(actor_response or ""))
    if field == "trust":
        cap = _TRUST_MILESTONE_STEP_CAP if milestone else _TRUST_MEANINGFUL_STEP_CAP
    else:
        cap = _AFFINITY_MILESTONE_STEP_CAP if milestone else _AFFINITY_MEANINGFUL_STEP_CAP

    diff = requested - current
    if abs(diff) <= cap:
        return requested
    return current + (cap if diff > 0 else -cap)


def _updates_for_db(
    update: RelationshipUpdate,
    target: RelationshipTarget,
    actor_response: str,
) -> dict[str, Any]:
    """Map model field names to RELATIONSHIP property names."""
    data: dict[str, Any] = {}
    affinity = _bounded_score_update(target.current.get("affinity"), update.affinity, "affinity", actor_response)
    trust = _bounded_score_update(target.current.get("trust"), update.trust, "trust", actor_response)
    if affinity is not None:
        data["affinity"] = affinity
    if trust is not None:
        data["trust"] = trust
    if update.rel_type:
        data["type"] = update.rel_type
    if update.current_status:
        data["current_status"] = update.current_status
    if update.summary:
        data["summary"] = update.summary
    return data


async def apply_scene_relationship_updates(
    actor_response: str,
    participant_ids: list[str],
    primary_pair: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Update secondary scene relationships that Pro can justify from the scene."""
    targets = await _fetch_relationship_targets(participant_ids, primary_pair)
    target_by_edge = {(target.source_id, target.target_id): target for target in targets}
    allowed_edges = set(target_by_edge)
    if not allowed_edges:
        return []

    try:
        plan = await _run_relationship_update(actor_response, targets)
    except Exception as exc:
        print(f"[RelationshipUpdater] failed and ignored: {exc}")
        return []

    applied: list[dict[str, Any]] = []
    for item in plan.relationship_updates:
        edge = (item.source_id, item.target_id)
        if edge not in allowed_edges:
            continue
        updates = _updates_for_db(item, target_by_edge[edge], actor_response)
        if not updates:
            continue
        await update_relationship_fields(item.source_id, item.target_id, updates)
        applied.append({"source_id": item.source_id, "target_id": item.target_id, **updates})

    if applied:
        print(f"[RelationshipUpdater] applied: {json.dumps(applied, ensure_ascii=False)}")
    return applied
