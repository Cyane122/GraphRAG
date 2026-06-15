# ================================
# src/simulation/state/extract/multi_character.py
#
# Actor response text에서 여러 NPC의 DynamicState 변경을 추출하고 DB에 반영합니다.
#
# Classes
#   - StateUpdateTarget : 상태 갱신 후보 캐릭터 정보
#   - CharacterStateUpdate : 단일 캐릭터 DynamicState 변경 계획
#   - MultiCharacterStatePlan : 다중 캐릭터 DynamicState 변경 계획
#
# Functions
#   - apply_multi_character_state_updates(actor_response: str, pc_id: str, participant_ids: list[str] | None = None, world_config: dict | None = None) -> list[dict[str, Any]] : 변경된 NPC 상태를 DB에 반영합니다.
# ================================

from __future__ import annotations

import asyncio
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
from src.simulation.state.apply.audit import _audit_state_updates, _sanitize_stress_level


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
    # 목적지 없이 현재 장면을 떠난 캐릭터 id 목록 (예: "B는 교실을 나갔다").
    exited_character_ids: list[str] = Field(default_factory=list)


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


def _location_lookup(locations: list[dict[str, str | None]]) -> dict[str, str]:
    """Return known location id-to-name lookup data."""
    return {
        str(item["id"]): str(item.get("name") or item["id"])
        for item in locations
        if item.get("id")
    }


def _text_contains_location_token(text: str, token: object) -> bool:
    """Return whether a non-trivial location token appears in the accepted response."""
    value = str(token or "").strip()
    if len(value) < 2:
        return False
    return value.lower() in text.lower()


# 자택 이동 근거 판정용: 집/방류 공간어가 목적지 격조사(에/으로/로, 출발격 '에서'는 제외)를
# 동반하고 직후에 이동/귀가 동사가 와야 자택 이동으로 본다. 소유격('~의 방')·출발격('방에서')·
# 단순 위치('방에 있다')만으로는 phantom 위치를 만들지 않는다.
_HOME_SPACE_DEST_RE = r"(?:집|방|아파트|자취방|원룸|기숙사)(?:에|으로|로)(?!서)"
_HOME_MOVE_VERB_RE = r"(?:갔|왔|돌아|들어|향했|향한|향하|도착|올라|귀가)"


def _personal_space_is_grounded(
    location_id: str,
    char_id: str,
    actor_response: str,
    targets: list[StateUpdateTarget],
) -> bool:
    """Allow generated personal-space ids only when the scene shows the character moving home."""
    if location_id != f"{char_id}_house":
        return False
    target = next((item for item in targets if item.id == char_id), None)
    if not target:
        return False
    aliases = _target_aliases(target)
    # alias 직후의 선택적 소유격(alias의)만 허용하고, alias와 공간어 사이에 다른 소유격 '의'가
    # 끼면 매칭하지 않는다. 그래야 "A는 B의 방에 들어갔다"가 A의 자택으로 오인되지 않는다.
    return any(
        alias in actor_response
        and re.search(
            rf"{re.escape(alias)}(?:\s*의)?[^의\n]{{0,18}}{_HOME_SPACE_DEST_RE}.{{0,10}}{_HOME_MOVE_VERB_RE}",
            actor_response,
        )
        for alias in aliases
    )


def _location_update_has_scene_evidence(
    location_id: str,
    char_id: str,
    actor_response: str,
    locations: list[dict[str, str | None]],
    targets: list[StateUpdateTarget],
) -> bool:
    """Return whether a location update target is explicitly named in the response."""
    lookup = _location_lookup(locations)
    return (
        _text_contains_location_token(actor_response, location_id)
        or _text_contains_location_token(actor_response, lookup.get(location_id))
        or _personal_space_is_grounded(location_id, char_id, actor_response, targets)
    )


def _state_field_lines(field_types: dict[str, str]) -> str:
    """Format live DynamicState columns for the state extraction prompt."""
    return "\n".join(
        f"- {name}: {field_type}"
        for name, field_type in sorted(field_types.items())
        if name != "id"
    )


def _compact_world_context_text(text: object, limit: int) -> str:
    """World prompt text를 다중 상태 추출 프롬프트에 맞게 길이 제한합니다."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def _render_state_world_context(world_config: dict | None) -> str:
    """월드/시나리오 규범을 다중 캐릭터 상태 추출용 컨텍스트로 렌더링합니다."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []
    world_lore = _compact_world_context_text(sections.get("world"), 1200)
    if world_lore:
        parts.append("### World Lore\n" + world_lore)
    scenario_lore = _compact_world_context_text(sections.get("scenario"), 4200)
    if scenario_lore:
        parts.append("### Scenario Lore\n" + scenario_lore)
    return "\n\n".join(parts) if parts else "(none)"


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


async def _fetch_dynamic_state_values(
    char_id: str,
    fields: list[str],
) -> dict[str, Any]:
    """현재 DynamicState 값을 로그용으로 조회합니다."""
    safe_fields = [
        field
        for field in fields
        if field and (field[0].isalpha() or field[0] == "_") and field.replace("_", "").isalnum()
    ]
    if not safe_fields:
        return {}
    return_clause = ", ".join(f"d.{field} AS {field}" for field in safe_fields)
    async with async_driver.session() as session:
        rec = await session.run(
            f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            RETURN {return_clause}
            """,
            char_id=char_id,
        )
        row = await rec.single()
    return dict(row) if row else {}


def _display_character_name(char_id: str, targets: list[StateUpdateTarget]) -> str:
    """로그에 표시할 캐릭터 이름을 반환합니다."""
    for target in targets:
        if target.id == char_id:
            return str(target.name or target.id)
    return char_id


def _display_value(value: object) -> str:
    """로그용 값 문자열을 만듭니다."""
    if value in (None, ""):
        return "(기존)"
    return str(value)


def _candidate_evidence_by_field(candidates: list[dict]) -> dict[str, str]:
    """커밋된 후보의 evidence를 field별로 찾기 쉽게 변환합니다."""
    result: dict[str, str] = {}
    for candidate in candidates:
        if candidate.get("commit_policy") != "commit":
            continue
        field = str(candidate.get("field") or "")
        evidence = str(candidate.get("evidence") or "").strip()
        if field and evidence:
            result[field] = evidence
    return result


def _format_state_change_lines(
    char_name: str,
    before: dict[str, Any],
    after: dict[str, Any],
    evidence_by_field: dict[str, str],
) -> list[str]:
    """커밋된 DynamicState 변경을 채팅형 한 줄 로그로 변환합니다."""
    lines: list[str] = []
    for field, new_value in after.items():
        old_value = before.get(field)
        if old_value == new_value:
            continue
        evidence = evidence_by_field.get(field) or "evidence unavailable"
        lines.append(
            f"{char_name} {field}: {_display_value(old_value)} -> {_display_value(new_value)} / {evidence}"
        )
    return lines


async def _run_multi_character_state_update(
    actor_response: str,
    targets: list[StateUpdateTarget],
    world_config: dict | None = None,
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
    world_context_block = _render_state_world_context(world_config)
    prompt = f"""## Characters
{_target_lines(targets)}

## Locations
{_location_lines(known_locations)}

## World/Scenario Context
{world_context_block}

Extract DynamicState updates for explicitly changed characters. PC is an in-world character — update when explicitly changed. Omit unchanged. Korean particles still refer to same target; match aliases w/ particles.
Use World/Scenario Context when interpreting whether an action implies negative emotion, stress, injury, resistance, or social consequence. If the scenario says an action is ordinary, expected, remapped, or non-alarming, do NOT infer moral shock, fear, anger, stress, injury, or negative thoughts from that action unless the scene explicitly states a lasting state change.
For outfit/clothing, update the character who owns or wears the clothing, not the helper/actor. If "A가 B의 잠옷을 벗긴다", update B.outfit, never A.outfit.

Hard field rule:
- Use only DynamicState fields listed below. Never invent new keys, traits, axes, counters, tags, or attributes.
- If a changed trait has no exact field below, omit it entirely.
- Do not use synonyms or new schema names; return only listed field names.

location_id: update when a character explicitly moves, leaves, arrives, follows someone to another place, leads someone to another place, or accompanies a group to another place.
- If "A follows B to LOCATION" / "A가 B를 따라 LOCATION으로 이동" / "B가 A를 데리고 LOCATION으로 이동", update BOTH A.location_id and B.location_id when both are known characters.
- If "A and B go/leave/enter/arrive at LOCATION", update every named moving character.
- Treat hallway/room/doorway/classroom/bathroom/etc. as a different location if it appears in Locations or can be represented by a concrete id.
- Do NOT update location for pacing, turning, approaching, or walking around inside the same current spot without a destination.
- Known location → exact id from list.
- Character's OWN personal space (own home / room / apt) → generate "{{char_id}}_house" (e.g. if char_id is "ko_haram" → "ko_haram_house"). These are auto-created.
- Unknown public/third-party location → omit.

Scene exits (no destination): if a character explicitly LEAVES the current scene with NO named/known destination (e.g. "B는 교실을 나갔다", "자리를 떴다", "먼저 나갔다", "사라졌다"), do NOT set location_id; instead put their char_id in "exited_character_ids". Only when the scene explicitly states they left; never infer from looking away, standing, or turning.

DynamicState fields:
{_state_field_lines(field_types)}

Rules: stress_level/workplace_stress_level → JSON number 0..10. Numeric → JSON numbers; boolean → JSON booleans. Do not return id.

Return ONLY valid JSON. Use real char_id values:
{{
  "character_updates": [
    {{"char_id": "<character_id>", "dynamic_state": {{"mood": "tired"}}}}
  ],
  "exited_character_ids": []
}}

Scene:
{actor_response[:3000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    try:
        response = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 4096,
                "response_mime_type": "application/json",
                "log_source": "multi_state_updater",
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


async def _resolve_exit_parent_location(char_id: str) -> str | None:
    """퇴장한 캐릭터를 옮길 상위 위치(PART_OF parent) id. 부모가 없으면 None(이동 미적용)."""
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (c:Character {id: $cid})-[:LOCATED_AT]->(l:Location)
            OPTIONAL MATCH (l)-[:PART_OF]->(p:Location)
            RETURN p.id AS parent_id
            """,
            cid=char_id,
        )
        row = await rec.single()
    if not row:
        return None
    parent_id = dict(row).get("parent_id")
    return str(parent_id) if parent_id else None


async def apply_multi_character_state_updates(
    actor_response: str,
    pc_id: str,
    participant_ids: list[str] | None = None,
    world_config: dict | None = None,
) -> list[dict[str, Any]]:
    """NPC별 DynamicState 업데이트를 감지하고 감사한 뒤 DB에 반영합니다."""
    targets = await _fetch_state_update_targets(pc_id, participant_ids=participant_ids)
    allowed_ids = {target.id for target in targets}
    if not allowed_ids:
        print(
            "[MultiStateUpdater] skipped: no state update targets "
            f"(participants={participant_ids or []}, pc_id={pc_id})"
        )
        return []

    try:
        plan = await _run_multi_character_state_update(actor_response, targets, world_config)
    except Exception as exc:
        print(f"[MultiStateUpdater] failed and ignored: {exc}")
        return []
    if not plan.character_updates and not plan.exited_character_ids:
        print(
            "[MultiStateUpdater] no candidate updates "
            f"(targets={[target.id for target in targets]})"
        )
        return []

    known_locations = await _fetch_known_locations()
    applied: list[dict[str, Any]] = []
    change_lines: list[str] = []
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
        if not state:
            continue
        location_id = state.pop("location_id", None)
        applied_state = dict(state)
        if location_id:
            applied_state["location_id"] = location_id
        before = await _fetch_dynamic_state_values(item.char_id, list(applied_state))
        if location_id:
            location_text = str(location_id)
            if _location_update_has_scene_evidence(
                location_text,
                item.char_id,
                actor_response,
                known_locations,
                targets,
            ):
                await _ensure_location_if_missing(location_text)
                await move_location(item.char_id, location_text)
            else:
                applied_state.pop("location_id", None)
                if not applied_state:
                    continue
        await update_dynamic_state(item.char_id, state)
        evidence_by_field = _candidate_evidence_by_field(candidates)
        change_lines.extend(_format_state_change_lines(
            _display_character_name(item.char_id, targets),
            before,
            applied_state,
            evidence_by_field,
        ))
        applied.append({"char_id": item.char_id, "dynamic_state": applied_state})

    # 목적지 없이 장면을 떠난 NPC는 현재 위치의 상위(PART_OF parent)로 옮겨 다음 턴
    # presence 조회에서 제외되게 한다. 상위 위치가 없으면 안전하게 건너뛴다(잔존).
    for exited_id in dict.fromkeys(plan.exited_character_ids or []):
        if exited_id not in allowed_ids or exited_id == pc_id:
            continue
        parent_location_id = await _resolve_exit_parent_location(exited_id)
        if not parent_location_id:
            print(f"[MultiStateUpdater] exit skipped (no parent location): {exited_id}")
            continue
        if await move_location(exited_id, parent_location_id):
            change_lines.append(
                f"{_display_character_name(exited_id, targets)} exited scene -> {parent_location_id}"
            )
            applied.append({"char_id": exited_id, "exited_to": parent_location_id})

    if change_lines:
        print("[MultiStateDiff]\n" + "\n".join(change_lines))
    elif plan.character_updates:
        print(
            "[MultiStateUpdater] no applied updates after audit "
            f"(candidates={len(plan.character_updates)})"
        )
    return applied
