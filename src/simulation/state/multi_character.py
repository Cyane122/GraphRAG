# ================================
# src/simulation/state/multi_character.py
#
# Actor response text에서 여러 NPC의 DynamicState 변경을 추출하고 DB에 반영합니다.
#
# Classes
#   - StateUpdateTarget : 상태 갱신 후보 캐릭터 정보
#   - CharacterStateUpdate : 단일 캐릭터 DynamicState 변경 계획
#   - MultiCharacterStatePlan : 다중 캐릭터 DynamicState 변경 계획
#
# Functions
#   - apply_multi_character_state_updates(actor_response: str, pc_id: str) -> list[dict[str, Any]] : 변경된 NPC 상태를 DB에 반영합니다.
# ================================

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.database import (
    async_driver,
    get_dynamic_state_field_types,
    move_location,
    update_dynamic_state,
)
from src.simulation.state.audit import _audit_state_updates, _sanitize_stress_level


class StateUpdateTarget(BaseModel):
    """LLM이 상태 갱신 대상으로 선택할 수 있는 캐릭터 정보입니다."""

    id: str
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    type: str | None = None


class CharacterStateUpdate(BaseModel):
    """단일 캐릭터에 적용할 DynamicState 변경 계획입니다."""

    char_id: str
    dynamic_state: dict[str, Any] = Field(default_factory=dict)


class MultiCharacterStatePlan(BaseModel):
    """한 장면에서 감지된 여러 캐릭터의 DynamicState 변경 계획입니다."""

    character_updates: list[CharacterStateUpdate] = Field(default_factory=list)


async def _fetch_state_update_targets(
    pc_id: str,
    participant_ids: list[str] | None = None,
) -> list[StateUpdateTarget]:
    """DB의 Character 목록에서 PC를 제외한 NPC 상태 갱신 후보를 조회합니다."""
    allowed_ids = {item for item in (participant_ids or []) if item}
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name, c.aliases AS aliases, c.type AS type
            """
        )
        rows = await result.fetch_all()

    targets: list[StateUpdateTarget] = []
    for row in rows:
        data = dict(row)
        char_id = data.get("id")
        if not char_id:
            continue
        if allowed_ids and char_id not in allowed_ids:
            continue
        targets.append(
            StateUpdateTarget(
                id=str(char_id),
                name=data.get("name"),
                aliases=list(data.get("aliases") or []),
                type=data.get("type"),
            )
        )
    return targets


def _target_lines(targets: list[StateUpdateTarget]) -> str:
    """프롬프트에 넣을 캐릭터 후보 목록을 안정적인 텍스트로 변환합니다."""
    return "\n".join(
        f"- {target.id}: name={target.name}, aliases={target.aliases}"
        for target in targets
    )


async def _fetch_known_locations() -> list[dict[str, str | None]]:
    """Location id/name pairs used to normalize explicit movement into location_id."""
    async with async_driver.session() as session:
        result = await session.run(
            """
            MATCH (l:Location)
            RETURN l.id AS id, l.name AS name
            ORDER BY l.id
            """
        )
        rows = await result.fetch_all()
    return [{"id": row["id"], "name": row["name"]} for row in rows if row["id"]]


def _location_lines(locations: list[dict[str, str | None]]) -> str:
    """Known location ids formatted for the state extraction prompt."""
    if not locations:
        return "- (none)"
    return "\n".join(
        f"- {item['id']}: {item.get('name') or item['id']}"
        for item in locations
    )


def _state_field_lines(field_types: dict[str, str]) -> str:
    """Format live DynamicState columns for the state extraction prompt."""
    return "\n".join(
        f"- {name}: {field_type}"
        for name, field_type in sorted(field_types.items())
        if name != "id"
    )


def _parse_state_plan(plan: object) -> MultiCharacterStatePlan:
    """LLM JSON 추출 결과를 모듈 경계용 Pydantic 모델로 정규화합니다."""
    if not isinstance(plan, dict):
        return MultiCharacterStatePlan()
    try:
        return MultiCharacterStatePlan.model_validate(plan)
    except ValueError:
        return MultiCharacterStatePlan()


async def _run_multi_character_state_update(
    actor_response: str,
    targets: list[StateUpdateTarget],
) -> MultiCharacterStatePlan:
    """Actor 응답에서 명시적으로 변한 NPC별 DynamicState 업데이트를 추출합니다."""
    from src.core.llm.client import extract_json_from_llm, get_model

    if not targets:
        return MultiCharacterStatePlan()

    known_locations, field_types = await asyncio.gather(
        _fetch_known_locations(),
        get_dynamic_state_field_types(),
    )

    system_instruction = (
        "You are a precise multi-character state manager for a Korean roleplay system. "
        "Update only the character who is explicitly described as changing. "
        "Never copy one character's state to another character."
    )
    prompt = f"""## Valid character targets
{_target_lines(targets)}

## Known location ids
{_location_lines(known_locations)}

## Task
Extract DynamicState updates for any listed character whose current state explicitly changed in the scene.
The PC/player is an in-world character and MUST be updated when explicitly changed.
Do not infer hidden changes. Omit unchanged characters.
Korean particles attached to names still refer to the same target.
Match target aliases even when topic, subject, object, or dative particles are attached.
Location change (conservative): Only update location_id when a character EXPLICITLY DEPARTS
the current scene and the destination is clearly a different known location (e.g., "went home",
"left for school", "headed to the café"). Do NOT update location_id merely because a character
is described as being at or walking around their current location. If the destination cannot be
matched to a known location id from the list above, omit location_id entirely.

Allowed DynamicState fields are the live schema columns below:
{_state_field_lines(field_types)}

Special field rules:
- location_id: only if the character clearly moved to one of the known location ids above.
  Map location names to ids from the known location list. Return the id exactly as listed.
- stress_level and workplace_stress_level: JSON number 0..10.
- Numeric schema fields must be JSON numbers, boolean schema fields must be JSON booleans.
- Do not return id.

Return ONLY valid JSON in this shape. Use real char_id values from the target list:
{{
  "character_updates": [
    {{"char_id": "<character_id>", "dynamic_state": {{"mood": "tired"}}}}
  ]
}}

Scene:
{actor_response[:3000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
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
        print("[MultiStateUpdater] timeout")
        return MultiCharacterStatePlan()

    raw_plan = extract_json_from_llm(response.text, source="multi_state_updater")
    return _parse_state_plan(raw_plan)


def _sanitize_state_update(state: dict[str, Any]) -> dict[str, Any]:
    """숫자 필드 등 DynamicState 업데이트 값을 DB 반영 전에 정리합니다."""
    sanitized_state = dict(state)
    for field in ("stress_level", "workplace_stress_level"):
        if field not in sanitized_state:
            continue
        sanitized = _sanitize_stress_level(sanitized_state[field])
        if sanitized is None:
            del sanitized_state[field]
        else:
            sanitized_state[field] = sanitized
    return sanitized_state


async def apply_multi_character_state_updates(
    actor_response: str,
    pc_id: str,
    participant_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """NPC별 DynamicState 업데이트를 감지하고 감사한 뒤 DB에 반영합니다."""
    targets = await _fetch_state_update_targets(pc_id, participant_ids=participant_ids)
    allowed_ids = {target.id for target in targets}
    if not allowed_ids:
        return []

    try:
        plan = await _run_multi_character_state_update(actor_response, targets)
    except Exception as exc:
        print(f"[MultiStateUpdater] failed and ignored: {exc}")
        return []

    applied: list[dict[str, Any]] = []
    for item in plan.character_updates:
        if item.char_id not in allowed_ids:
            continue
        state = _sanitize_state_update(item.dynamic_state)
        state, candidates = _audit_state_updates(state, actor_response, item.char_id)
        if candidates:
            print(f"[MultiStateDiff] {json.dumps(candidates, ensure_ascii=False)}")
        if not state:
            continue
        location_id = state.pop("location_id", None)
        if location_id:
            await move_location(item.char_id, str(location_id))
        await update_dynamic_state(item.char_id, state)
        applied_state = dict(state)
        if location_id:
            applied_state["location_id"] = location_id
        applied.append({"char_id": item.char_id, "dynamic_state": applied_state})

    if applied:
        print(f"[MultiStateUpdater] applied: {json.dumps(applied, ensure_ascii=False)}")
    return applied
