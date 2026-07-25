# ================================
# src/simulation/state/extract/primary.py
#
# Primary state extractor: DynamicState / relationship delta / event action
# for the main NPC in one Actor response turn.
#
# Functions
#   - _run_primary_update(actor_response, npc_id, pc_id, world_config, recent_context, active_event, allow_event) -> dict : Run one LLM extractor for state + relationship + event.
#   - _render_state_world_context(world_config: dict | None) -> str : Render World/Scenario lore for extractor prompt.
#   - _render_dynamic_state_field_policy(field_types: dict[str, str]) -> str : Render allowed DynamicState fields for extractor prompt.
#   - _compact_world_context_text(text: object, limit: int) -> str : Truncate world prompt text to a safe limit.
# ================================

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.database import get_dynamic_state_field_types
from src.simulation.state.importance import IMPORTANCE_RUBRIC


def _compact_world_context_text(text: object, limit: int) -> str:
    """World prompt text를 상태 추출 프롬프트에 넣을 수 있게 길이 제한합니다."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def _render_state_world_context(world_config: dict | None) -> str:
    """월드/시나리오 규범을 상태 추출용 컨텍스트로 렌더링합니다."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []
    world_lore = _compact_world_context_text(sections.get("world"), 1200)
    if world_lore:
        parts.append("### World Lore\n" + world_lore)
    scenario_lore = _compact_world_context_text(sections.get("scenario"), 4200)
    if scenario_lore:
        parts.append("### Scenario Lore\n" + scenario_lore)
    return "\n\n".join(parts) if parts else "(none)"


def _render_dynamic_state_field_policy(field_types: dict[str, str]) -> str:
    """Render allowed DynamicState fields for extractor prompt."""
    lines = [
        f"- {name}: {field_type}"
        for name, field_type in sorted(field_types.items())
        if name != "id"
    ]
    return "\n".join(lines) if lines else "- (schema unavailable; use only named fields below)"


async def _run_primary_update(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    world_config:   dict | None = None,
    recent_context: str = "",
    active_event:   dict | None = None,
    allow_event:    bool = True,
) -> dict:
    """
    Run one extractor for main DynamicState, relationship deltas, and gated event actions.

    Returns:
        {dynamic_state, relationship_delta, action, new_event,
         ts_acceptance_delta?, northern_attachment_delta?}
    """
    from src.core.llm.client import get_model, extract_json_from_llm

    ts_scoring = bool(world_config and world_config.get("ts_scoring_enabled"))
    ts_section = ""
    ts_json_fields = ""
    if ts_scoring:
        ts_section = f"""
## TS/North Acceptance Scoring (ONLY for NPC {npc_id})

ts_acceptance_delta = involuntary biological/physical feminine submission this turn.
DEFAULT 0. Increment only for:
  +1: Subtle involuntary reaction (caught breath, hesitation, warmth suppressed)
  +2: Clear involuntary feminine response she cannot deny
  +3: Significant biological defeat (menstrual pain forcing yield, arousal until she flees)
  +5: Major submission, shattering of male self. Extremely rare.
NEVER negative. Max +5 per turn.

northern_attachment_delta = genuine warmth/solidarity toward North characters this turn.
DEFAULT 0. Increment only for:
  +1: Small warmth moment from a North character
  +2: Genuine solidarity or recognition moment
  +3: Significant emotional shift. Rare.
NEVER negative. Max +3 per turn.
"""
        ts_json_fields = '\n  "ts_acceptance_delta": 0,\n  "northern_attachment_delta": 0,'

    system_instruction = f"""You are a precise post-response state manager for a Korean roleplay system.
Analyze the accepted scene. Return updates ONLY for Main NPC ({npc_id}) and the ({npc_id})<->{pc_id}) relationship.
CRITICAL: Do NOT assign secondary characters' injuries, emotions, or clothing to {npc_id}."""

    if active_event:
        turns = active_event.get("turn_count", 1)
        active_block = f"""
Active event [{active_event['id']}] — turn {turns} — {active_event.get('summary', '')}
→ "continue": same event still in progress. "close": event has concluded/topic changed. "close_create": closes AND a separate new event starts this turn.
"""
        actions_hint = '"continue"/"close"/"close_create"(close+new)/"create"(new, ignore active)/"none"'
    else:
        active_block = ""
        actions_hint = '"create"(any concrete event, importance 0-10)/"none"'

    event_block = f"""
Event action is enabled.
{IMPORTANCE_RUBRIC}
{active_block}
action: {actions_hint}
When an active event exists, close it as soon as the activity concludes, the characters leave, time advances past it, or the scene topic shifts. Routine daily activities such as meals should not remain active after their visible conclusion.
For new events, skip routine meals, sitting, waiting, casual small talk, and atmosphere unless the turn creates a durable state change or a multi-turn scene anchor.
For create/close_create include event_data fields:
  id: {{location}}_{{description}}_{{YYYYMMDD_HHMM}}
  summary: 1-2 sentence Korean factual record; only observed facts, no subjective distortion or speculation
  memory_type: episodic/emotional/relational
  importance: 0-10 | impact: brief phrase
  importance >= 7 → new_relationship_status: 1-2 English sentences about how they regard each other after the event; not their current physical activity. Exclude current actions, positions, scene activity, and "currently/now" details.

Context: {recent_context[-2000:] if recent_context else "(none)"}
""" if allow_event else """
Event action is disabled for this turn. Return action="none" and new_event=null.
"""

    try:
        field_types = await get_dynamic_state_field_types()
    except Exception as exc:
        print(f"[StateUpdater] DynamicState schema fetch failed (prompt fallback): {exc}")
        field_types = {}

    world_context_block = _render_state_world_context(world_config)
    state_field_policy = _render_dynamic_state_field_policy(field_types)
    prompt = f"""LITERAL=physical facts (injury/illness/clothing). FIGURATIVE=emotion/metaphor — never touch physical_condition/injury_detail.

World/Scenario Context:
{world_context_block}

Use World/Scenario Context when interpreting whether an action implies negative emotion,
stress, injury, resistance, or social consequence. If the scenario says an action is ordinary,
expected, remapped, or non-alarming, do NOT infer moral shock, fear, anger, stress, injury, or
negative thoughts from that action unless the accepted scene explicitly states a lasting state change.

DynamicState — extract ONLY changed existing fields.
Allowed DynamicState fields are exactly:
{state_field_policy}

Hard field rule:
- Never invent new DynamicState keys, traits, axes, counters, tags, or attributes.
- If a changed trait is not already an allowed field above, omit it entirely.
- Do not use synonyms or new schema names; choose an allowed field only when it exactly fits.

Preferred field meanings:
mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
emotional_state: Korean phrase (e.g. "설렘","불안","안도")
mental_condition: stable/stressed/anxious/depressed/exhausted
stress_level: 0-10 int. 10=breakdown,5=conflict,3=tension,0=calm
workplace_stress_level: 0-10 int. 10=public disaster,6=pressure,2=minor,0=none
outfit: only if explicitly described (Korean,≤50ch)
injury_marks: "없음" / description — only if changed
LITERAL only: physical_condition(healthy/fatigued/injured/ill/hospitalized), injury_detail
Rare (omit if not triggered): age(birthday/year pass), circle_level(mana breakthrough), robe_grade(bronze/silver/gold/platinum/crimson)
All numeric = JSON numbers, not strings.

Relationship delta: int or null.
null=routine/proximity/embarrassment/no change.
±1=small visible shift. ±2=clear scene-level shift. ±3=rare milestone (confession/betrayal/rescue/reconciliation/near-breakup).
Sex (incl. first intimacy) ≠ affinity milestone. No growth from intimacy/politeness/arousal alone.
{ts_section}
{event_block}

Return ONLY valid JSON:
{{
  "dynamic_state": {{}},{ts_json_fields}
  "relationship_delta": null,
  "action": "none",
  "new_event": null
}}

Scene:
{actor_response[:4000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    try:
        response = await model.generate_content_async(
            prompt,
            generation_config={"temperature": 0.0, "max_output_tokens": 4096,
                               "response_mime_type": "application/json",
                               "log_source": "primary_state_updater"},
        )
    except TimeoutError:
        print("[StateUpdater] primary updater timeout")
        return {}

    plan = extract_json_from_llm(response.text, source="primary_state_updater")
    return plan if isinstance(plan, dict) else {}
