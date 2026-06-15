# ================================
# src/agents/manager/integrated_planner.py
#
# Shadow-first Pro planner artifact generation for Manager turn preparation.
#
# Classes
#   - IntegratedManagerPlan : Commit-scoped manager planning artifact.
#
# Functions
#   - maybe_run_integrated_planner(user_input: str, recent_story: str, thread_id: str | None, commit_id: str | None, legacy_plan: dict, mode: str) -> IntegratedManagerPlan | None : Run or reuse the integrated manager planner.
#   - validated_context_plan(plan: IntegratedManagerPlan) -> dict[str, Any] | None : Return a safe integrated ContextPlan section.
#   - write_manager_shadow_diff(thread_id: str | None, commit_id: str | None, legacy_plan: dict, integrated_plan: IntegratedManagerPlan | None, latency_ms: int | None, failure: str | None = None, artifact_reuse: bool = False) -> None : Persist a legacy/integrated diff summary.
# ================================

from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from src.config import MODEL_MANAGER_PLANNER
from src.core.commit_artifacts import read_artifact, text_hash, write_artifact
from src.core.llm.client import extract_json_from_llm, get_model


SCHEMA_VERSION = "manager_plan.v1"
PROMPT_VERSION = "manager-integrated-shadow-v1"


class IntegratedManagerPlan(BaseModel):
    """Serializable Manager planning artifact produced by the integrated planner."""

    schema_version: str = SCHEMA_VERSION
    commit_id: str
    thread_id: str | None = None
    user_input_hash: str
    model_name: str = MODEL_MANAGER_PLANNER
    prompt_version: str = PROMPT_VERSION
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scene_types: list[str] = Field(default_factory=list)
    time_parse: dict[str, Any] = Field(default_factory=dict)
    context_plan: dict[str, Any] = Field(default_factory=dict)
    present_character_hints: list[str] = Field(default_factory=list)
    personal_fact_candidates: list[dict[str, Any]] = Field(default_factory=list)
    kakao_reply_intent: dict[str, Any] = Field(default_factory=dict)


def _list_value(value: object) -> list[str]:
    """Normalize a JSON value to a list of non-empty strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def validated_context_plan(plan: IntegratedManagerPlan) -> dict[str, Any] | None:
    """Return a safe integrated ContextPlan section or None when it is unusable."""
    context_plan = dict(plan.context_plan or {})
    if not context_plan:
        return None

    required_systems = _list_value(context_plan.get("required_systems"))
    required_nodes = _list_value(context_plan.get("required_nodes"))
    query_focus = _list_value(context_plan.get("query_focus"))
    priority_order = _list_value(context_plan.get("priority_order"))
    if not required_systems or not query_focus:
        return None

    context_plan["required_systems"] = required_systems
    context_plan["required_nodes"] = required_nodes
    context_plan["query_focus"] = query_focus
    context_plan["priority_order"] = priority_order or required_systems
    context_plan.setdefault("skip_systems", [])
    context_plan.setdefault("scene_type", (plan.scene_types or ["daily"])[0])
    context_plan.setdefault("scene_modifiers", (plan.scene_types or [])[1:])
    try:
        context_plan["importance"] = int(context_plan.get("importance") or 0)
    except (TypeError, ValueError):
        context_plan["importance"] = 0
    return context_plan


def _artifact_matches(payload: dict, commit_id: str, user_input_hash: str) -> bool:
    """Return whether a stored Manager artifact can be reused."""
    return (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("commit_id") == commit_id
        and payload.get("user_input_hash") == user_input_hash
    )


def _diff_lists(left: list[Any], right: list[Any]) -> dict[str, list[Any]]:
    """Return added/removed list values using stable string identity."""
    left_keys = {str(item): item for item in left or []}
    right_keys = {str(item): item for item in right or []}
    return {
        "added": [right_keys[key] for key in sorted(set(right_keys) - set(left_keys))],
        "removed": [left_keys[key] for key in sorted(set(left_keys) - set(right_keys))],
    }


def _manager_prompt(user_input: str, recent_story: str, legacy_plan: dict) -> str:
    """Build the JSON-only integrated planner prompt."""
    return f"""Analyze this roleplay turn for read-only Manager planning.

Return ONLY valid JSON with these keys:
scene_types: list of concise scene labels
time_parse: object with action_type, elapsed_minutes, new_location_id, reason when inferable
context_plan: object with required_systems, required_nodes, query_focus, priority_order, importance when inferable
present_character_hints: list of character ids or names directly likely present
personal_fact_candidates: list of objects, each with fact_text, normalized_key, evidence_quote, confidence, reason
kakao_reply_intent: object with should_reply boolean, target_room_id string/null, reason

Every candidate object must include evidence_quote, confidence between 0 and 1, and reason.
Do not invent facts not evidenced by current input or recent story.

Current user input:
{user_input}

Recent story:
{recent_story}

Legacy planning summary for comparison only:
{legacy_plan}
"""


async def maybe_run_integrated_planner(
    user_input: str,
    recent_story: str,
    thread_id: str | None,
    commit_id: str | None,
    legacy_plan: dict,
    mode: str,
) -> IntegratedManagerPlan | None:
    """Run or reuse the integrated manager planner when shadow/integrated mode is enabled."""
    if mode not in {"shadow", "integrated"} or not commit_id:
        return None

    user_input_hash = text_hash(user_input)
    stored = read_artifact(thread_id, commit_id, "integrated_manager_plan.json")
    if stored and _artifact_matches(stored, commit_id, user_input_hash):
        try:
            plan = IntegratedManagerPlan.model_validate(stored)
            write_manager_shadow_diff(thread_id, commit_id, legacy_plan, plan, 0, artifact_reuse=True)
            return plan
        except ValidationError:
            pass

    started = perf_counter()
    failure: str | None = None
    plan: IntegratedManagerPlan | None = None
    try:
        model = get_model(
            MODEL_MANAGER_PLANNER,
            system_prompt="You are a conservative JSON planner for a graph roleplay Manager.",
        )
        response = await model.generate_content_async(
            _manager_prompt(user_input, recent_story, legacy_plan),
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 3072,
                "response_mime_type": "application/json",
                "log_source": "integrated_manager_planner",
            },
        )
        raw = extract_json_from_llm(response.text, source="integrated_manager_planner")
        if not isinstance(raw, dict):
            raise ValueError("planner returned non-object JSON")
        raw.update({
            "schema_version": SCHEMA_VERSION,
            "commit_id": commit_id,
            "thread_id": thread_id,
            "user_input_hash": user_input_hash,
            "model_name": MODEL_MANAGER_PLANNER,
            "prompt_version": PROMPT_VERSION,
        })
        plan = IntegratedManagerPlan.model_validate(raw)
        write_artifact(thread_id, commit_id, "integrated_manager_plan.json", plan.model_dump(mode="json"))
    except Exception as exc:
        failure = str(exc)
        print(f"[IntegratedManager] planner failed ({mode}); legacy remains active: {exc}")
    finally:
        latency_ms = int((perf_counter() - started) * 1000)
        write_manager_shadow_diff(thread_id, commit_id, legacy_plan, plan, latency_ms, failure)
    return plan


def write_manager_shadow_diff(
    thread_id: str | None,
    commit_id: str | None,
    legacy_plan: dict,
    integrated_plan: IntegratedManagerPlan | None,
    latency_ms: int | None,
    failure: str | None = None,
    artifact_reuse: bool = False,
) -> None:
    """Persist a compact legacy-vs-integrated manager diff artifact."""
    if not commit_id:
        return
    integrated = integrated_plan.model_dump(mode="json") if integrated_plan else {}
    legacy_context = legacy_plan.get("context_plan") or {}
    integrated_context = integrated.get("context_plan") or {}
    payload = {
        "schema_version": "shadow_diff.v1",
        "commit_id": commit_id,
        "thread_id": thread_id,
        "manager": {
            "mode": "shadow_or_integrated",
            "latency_ms": latency_ms,
            "parse_failure_count": 1 if failure else 0,
            "global_fallback_count": 1 if failure else 0,
            "artifact_reuse_count": 1 if artifact_reuse else 0,
            "failure": failure,
            "scene_type_diff": _diff_lists(legacy_plan.get("scene_types") or [], integrated.get("scene_types") or []),
            "elapsed_minutes_diff": {
                "legacy": (legacy_plan.get("time_parse") or {}).get("elapsed_minutes"),
                "integrated": (integrated.get("time_parse") or {}).get("elapsed_minutes"),
            },
            "context_required_systems_diff": _diff_lists(
                legacy_context.get("required_systems") or [],
                integrated_context.get("required_systems") or [],
            ),
            "context_query_focus_diff": _diff_lists(
                legacy_context.get("query_focus") or [],
                integrated_context.get("query_focus") or [],
            ),
            "personal_fact_candidate_count": len(integrated.get("personal_fact_candidates") or []),
        },
    }
    write_artifact(thread_id, commit_id, "shadow_diff.json", payload)
