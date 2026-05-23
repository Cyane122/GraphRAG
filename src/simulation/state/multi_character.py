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
import re
from typing import Any

from pydantic import BaseModel, Field

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.database import (
    async_driver,
    ensure_location,
    get_dynamic_state_field_types,
    move_location,
    update_dynamic_state,
)
from src.simulation.state.audit import _audit_state_updates, _sanitize_stress_level


_CLOTHING_TERMS_RE = (
    r"옷|잠옷|상의|하의|셔츠|블라우스|티셔츠|바지|치마|원피스|교복|유니폼|"
    r"코트|재킷|가디건|브래지어|브라|속옷|팬티|스타킹|양말"
)
_UNDRESS_VERBS_RE = r"벗기|벗겨|벗겼|벗김|벗어|벗었|벗고|내리|내렸|풀어|풀었|열어|열었"


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


def _target_aliases(target: StateUpdateTarget) -> list[str]:
    """Return unique non-empty aliases that can appear in Korean prose."""
    aliases = [target.name or "", target.id, *target.aliases]
    unique: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = str(alias or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _find_possessive_clothing_owner(
    actor_response: str,
    targets: list[StateUpdateTarget],
) -> str | None:
    """Find the character whose clothing is explicitly undressed in Korean possessive phrasing."""
    text = actor_response or ""
    for target in targets:
        for alias in _target_aliases(target):
            escaped_alias = re.escape(alias)
            pattern = (
                rf"{escaped_alias}\s*(?:의|가\s+입은|이\s+입은)\s*"
                rf".{{0,30}}(?:{_CLOTHING_TERMS_RE}).{{0,30}}(?:{_UNDRESS_VERBS_RE})"
            )
            if re.search(pattern, text):
                return target.id
    return None


def _redistribute_possessive_outfit_updates(
    updates: list[CharacterStateUpdate],
    actor_response: str,
    targets: list[StateUpdateTarget],
) -> list[CharacterStateUpdate]:
    """Move misattributed outfit changes to the explicit clothing owner."""
    owner_id = _find_possessive_clothing_owner(actor_response, targets)
    if not owner_id:
        return updates

    redistributed: dict[str, dict[str, Any]] = {}
    for item in updates:
        state = dict(item.dynamic_state)
        outfit = state.pop("outfit", None)
        if state:
            redistributed.setdefault(item.char_id, {}).update(state)
        if outfit is not None:
            redistributed.setdefault(owner_id, {})["outfit"] = outfit

    return [
        CharacterStateUpdate(char_id=char_id, dynamic_state=state)
        for char_id, state in redistributed.items()
        if state
    ]


async def _ensure_location_if_missing(location_id: str) -> None:
    """DB에 없는 위치만 최소 정보로 생성한다. 이미 존재하면 아무것도 하지 않는다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (l:Location {id: $id}) RETURN l.id AS id", id=location_id
        )
        if await rec.single():
            return
    name = location_id.replace("_", " ").strip()
    await ensure_location(location_id, name=name, description="", prompt_priority=6)
    print(f"[MultiStateUpdater] 새 위치 자동 생성: {location_id}")


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
        "Precise multi-character state manager for Korean roleplay. "
        "Update only explicitly changed characters. Never copy one character's state to another."
    )
    prompt = f"""## Characters
{_target_lines(targets)}

## Locations
{_location_lines(known_locations)}

Extract DynamicState updates for explicitly changed characters. PC is an in-world character — update when explicitly changed. Omit unchanged. Korean particles still refer to same target; match aliases w/ particles.
For outfit/clothing, update the character who owns or wears the clothing, not the helper/actor. If "A가 B의 잠옷을 벗긴다", update B.outfit, never A.outfit.
location_id: only when character EXPLICITLY DEPARTS to a clearly different location. Do NOT update if still at / walking around current location.
- Known location → exact id from list.
- Character's OWN personal space (own home / room / apt) → generate "{{char_id}}_house" (e.g. if char_id is "ko_haram" → "ko_haram_house"). These are auto-created.
- Unknown public/third-party location → omit.

DynamicState fields:
{_state_field_lines(field_types)}

Rules: stress_level/workplace_stress_level → JSON number 0..10. Numeric → JSON numbers; boolean → JSON booleans. Do not return id.

Return ONLY valid JSON. Use real char_id values:
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
    updates = _redistribute_possessive_outfit_updates(
        plan.character_updates,
        actor_response,
        targets,
    )
    for item in updates:
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
            await _ensure_location_if_missing(str(location_id))
            await move_location(item.char_id, str(location_id))
        await update_dynamic_state(item.char_id, state)
        applied_state = dict(state)
        if location_id:
            applied_state["location_id"] = location_id
        applied.append({"char_id": item.char_id, "dynamic_state": applied_state})

    if applied:
        print(f"[MultiStateUpdater] applied: {json.dumps(applied, ensure_ascii=False)}")
    return applied
