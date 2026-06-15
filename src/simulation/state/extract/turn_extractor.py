# ================================
# src/simulation/state/extract/turn_extractor.py
#
# Unified accepted-turn fact extraction and artifact reuse.
#
# Classes
#   - AcceptedTurnFacts : Commit-scoped accepted response extraction artifact.
#
# Functions
#   - load_or_extract_turn_facts(actor_response: str, user_input: str, thread_id: str | None, commit_id: str | None, npc_id: str, pc_id: str, scene_types: list[str] | None, scene_chars: list[str] | None, mode: str) -> tuple[AcceptedTurnFacts | None, dict] : Reuse or run the unified turn extractor.
#   - facts_to_primary_plan(facts: AcceptedTurnFacts, allow_event: bool, npc_id: str, pc_id: str) -> dict : Convert accepted facts to the legacy primary updater plan shape.
#   - write_extractor_shadow_diff(thread_id: str | None, commit_id: str | None, facts: AcceptedTurnFacts | None, legacy_plan: dict | None, metrics: dict) -> None : Persist extractor metrics and candidate summaries.
# ================================

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.config import MODEL_TURN_EXTRACTOR
from src.core.commit_artifacts import read_artifact, text_hash, write_artifact
from src.core.database import get_dynamic_state_field_types
from src.core.llm.client import extract_json_from_llm, get_model
from src.simulation.state.importance import IMPORTANCE_RUBRIC


SCHEMA_VERSION = "accepted_turn_facts.v1"
# v3: memory_candidates에 signals/source_type/suggested_memory_type 필드 추가
PROMPT_VERSION = "turn-extractor-unified-v3"


class AcceptedTurnFacts(BaseModel):
    """Serializable accepted-turn fact artifact produced by the unified extractor."""

    schema_version: str = SCHEMA_VERSION
    commit_id: str
    thread_id: str | None = None
    user_input_hash: str
    actor_response_hash: str
    model_name: str = MODEL_TURN_EXTRACTOR
    prompt_version: str = PROMPT_VERSION
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dynamic_state_candidates: list[dict[str, Any]] = Field(default_factory=list)
    relationship_signals: list[dict[str, Any]] = Field(default_factory=list)
    event_candidates: list[dict[str, Any]] = Field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    secondary_relationship_signals: list[dict[str, Any]] = Field(default_factory=list)


def _artifact_matches(
    payload: dict,
    commit_id: str,
    user_input_hash: str,
    actor_response_hash: str,
) -> bool:
    """Return whether a stored extractor artifact can be reused."""
    # prompt_version 도 일치해야 재사용한다. 프롬프트(루브릭 등)가 바뀌면 구버전 캐시를 무효화.
    return (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("prompt_version") == PROMPT_VERSION
        and payload.get("commit_id") == commit_id
        and payload.get("user_input_hash") == user_input_hash
        and payload.get("actor_response_hash") == actor_response_hash
    )


def _candidate_is_supported(item: dict) -> bool:
    """Return whether a candidate has minimum evidence and confidence."""
    evidence = str(item.get("evidence_quote") or item.get("evidence") or "").strip()
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return bool(evidence) and confidence >= 0.55 and bool(str(item.get("reason") or "").strip())


def _extractor_prompt(
    actor_response: str,
    user_input: str,
    npc_id: str,
    pc_id: str,
    scene_types: list[str] | None,
    scene_chars: list[str] | None,
    allowed_dynamic_state_fields: list[str] | None = None,
) -> str:
    """Build the JSON-only accepted-turn extractor prompt."""
    allowed_fields = ", ".join(allowed_dynamic_state_fields or [])
    if not allowed_fields:
        allowed_fields = "schema unavailable; use only obvious existing DynamicState fields"
    return f"""Extract durable facts from an accepted roleplay turn.

Return ONLY valid JSON with these keys:
dynamic_state_candidates: list of objects with character_id, field, new_value, evidence_quote, confidence, reason
relationship_signals: list of objects with source_id, target_id, affinity_delta, evidence_quote, confidence, reason
event_candidates: list of objects with summary, impact, importance, memory_type, new_relationship_status, evidence_quote, confidence, reason
memory_candidates: list of objects with character_id, summary, memory_type, suggested_memory_type, signals, source_type, evidence_quote, confidence, reason
secondary_relationship_signals: list of objects with source_id, target_id, affinity_delta, current_status, evidence_quote, confidence, reason

Rules:
- Extract only literal, durable changes evidenced by the Actor response.
- DynamicState field must be one of these existing fields only: {allowed_fields}.
- Never invent new DynamicState keys, traits, axes, counters, tags, or attributes.
- If a changed trait has no exact allowed DynamicState field, omit it entirely.
- Figurative language must not become physical state.
- Routine politeness/proximity should not create large relationship deltas.
- Do not extract goal, item, secret, or organic/pregnancy facts in this phase.
- Every candidate must include evidence_quote, confidence between 0 and 1, and reason.
- memory_candidates.signals: list any applicable signal tags from [promise, appointment, secret, first_time, misunderstanding, conflict, reconciliation, betrayal, boundary, gift, item_anchor, debt, favor, identity, emotional_wound, gossip]. Use [] if none apply.
- memory_candidates.source_type: one of "direct_experience" | "hearsay" | "inference" | "gossip".
- memory_candidates.suggested_memory_type: one of "promise" | "misunderstanding" | "gossip" | "relational" | "item" | "episodic" | "emotional".

Event importance scoring (for each event_candidates.importance):
{IMPORTANCE_RUBRIC}

Primary NPC: {npc_id}
PC: {pc_id}
Scene types: {scene_types or []}
Scene chars: {scene_chars or []}

User input:
{user_input}

Accepted Actor response:
{actor_response}
"""


async def load_or_extract_turn_facts(
    actor_response: str,
    user_input: str,
    thread_id: str | None,
    commit_id: str | None,
    npc_id: str,
    pc_id: str,
    scene_types: list[str] | None,
    scene_chars: list[str] | None,
    mode: str,
) -> tuple[AcceptedTurnFacts | None, dict]:
    """Reuse or run the unified extractor for shadow/unified modes."""
    metrics = {
        "latency_ms": None,
        "parse_failure_count": 0,
        "global_fallback_count": 0,
        "artifact_reuse_count": 0,
    }
    if mode not in {"shadow", "unified"} or not commit_id:
        return None, metrics

    user_input_hash = text_hash(user_input)
    actor_response_hash = text_hash(actor_response)
    stored = read_artifact(thread_id, commit_id, "accepted_turn_facts.json")
    if stored and _artifact_matches(stored, commit_id, user_input_hash, actor_response_hash):
        try:
            metrics["artifact_reuse_count"] = 1
            return AcceptedTurnFacts.model_validate(stored), metrics
        except ValidationError:
            pass

    started = perf_counter()
    try:
        try:
            field_types = await get_dynamic_state_field_types()
        except Exception as exc:
            print(f"[TurnExtractor] DynamicState schema fetch failed (prompt fallback): {exc}")
            field_types = {}
        allowed_fields = sorted(name for name in field_types if name != "id")
        model = get_model(
            MODEL_TURN_EXTRACTOR,
            system_prompt="You are a conservative JSON extractor for accepted roleplay turns.",
        )
        response = await model.generate_content_async(
            _extractor_prompt(
                actor_response,
                user_input,
                npc_id,
                pc_id,
                scene_types,
                scene_chars,
                allowed_dynamic_state_fields=allowed_fields,
            ),
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 4096,
                "response_mime_type": "application/json",
                "log_source": "turn_extractor",
            },
        )
        raw = extract_json_from_llm(response.text, source="unified_turn_extractor")
        if not isinstance(raw, dict):
            raise ValueError("extractor returned non-object JSON")
        raw.update({
            "schema_version": SCHEMA_VERSION,
            "commit_id": commit_id,
            "thread_id": thread_id,
            "user_input_hash": user_input_hash,
            "actor_response_hash": actor_response_hash,
            "model_name": MODEL_TURN_EXTRACTOR,
            "prompt_version": PROMPT_VERSION,
        })
        facts = AcceptedTurnFacts.model_validate(raw)
        write_artifact(thread_id, commit_id, "accepted_turn_facts.json", facts.model_dump(mode="json"))
        return facts, metrics
    except Exception as exc:
        metrics["parse_failure_count"] = 1
        metrics["global_fallback_count"] = 1
        print(f"[TurnExtractor] extractor failed ({mode}); legacy remains active: {exc}")
        return None, metrics
    finally:
        metrics["latency_ms"] = int((perf_counter() - started) * 1000)


def facts_to_primary_plan(facts: AcceptedTurnFacts, allow_event: bool, npc_id: str, pc_id: str) -> dict:
    """Convert accepted facts into the legacy primary updater plan shape."""
    state: dict[str, Any] = {}
    for item in facts.dynamic_state_candidates:
        if not isinstance(item, dict) or not _candidate_is_supported(item):
            continue
        character_id = str(item.get("character_id") or npc_id).strip()
        if character_id != npc_id:
            continue
        field = str(item.get("field") or "").strip()
        if field:
            state[field] = item.get("new_value")

    relationship_delta = None
    for item in facts.relationship_signals:
        if not isinstance(item, dict) or not _candidate_is_supported(item):
            continue
        pair = {str(item.get("source_id") or "").strip(), str(item.get("target_id") or "").strip()}
        if pair and not {npc_id, pc_id}.issubset(pair):
            continue
        relationship_delta = item.get("affinity_delta")
        break

    event_candidate = None
    for item in facts.event_candidates:
        if not isinstance(item, dict) or not _candidate_is_supported(item):
            continue
        event_candidate = {
            "summary": item.get("summary"),
            "impact": item.get("impact") or item.get("reason"),
            "importance": item.get("importance", 0),
            "memory_type": item.get("memory_type") or "episodic",
            "new_relationship_status": item.get("new_relationship_status"),
        }
        break

    return {
        "dynamic_state": state,
        "relationship_delta": relationship_delta,
        "action": "create" if allow_event and event_candidate else "none",
        "new_event": event_candidate if allow_event else None,
    }


def write_extractor_shadow_diff(
    thread_id: str | None,
    commit_id: str | None,
    facts: AcceptedTurnFacts | None,
    legacy_plan: dict | None,
    metrics: dict,
) -> None:
    """Persist extractor metrics and candidate counts into the shadow diff artifact."""
    if not commit_id:
        return
    existing = read_artifact(thread_id, commit_id, "shadow_diff.json") or {
        "schema_version": "shadow_diff.v1",
        "commit_id": commit_id,
        "thread_id": thread_id,
    }
    fact_payload = facts.model_dump(mode="json") if facts else {}
    existing["extractor"] = {
        "latency_ms": metrics.get("latency_ms"),
        "parse_failure_count": metrics.get("parse_failure_count", 0),
        "global_fallback_count": metrics.get("global_fallback_count", 0),
        "artifact_reuse_count": metrics.get("artifact_reuse_count", 0),
        "section_fallback_count": 0,
        "audit_reject_count": 0,
        "legacy_plan_present": bool(legacy_plan),
        "dynamic_state_candidate_count": len(fact_payload.get("dynamic_state_candidates") or []),
        "relationship_signal_count": len(fact_payload.get("relationship_signals") or []),
        "event_candidate_count": len(fact_payload.get("event_candidates") or []),
        "memory_candidate_count": len(fact_payload.get("memory_candidates") or []),
        "secondary_relationship_signal_count": len(fact_payload.get("secondary_relationship_signals") or []),
    }
    write_artifact(thread_id, commit_id, "shadow_diff.json", existing)
