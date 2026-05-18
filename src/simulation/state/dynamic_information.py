# ================================
# src/simulation/state/dynamic_information.py
#
# Extract and persist slow-changing DynamicInformation updates from accepted
# actor responses.
#
# Functions
#   - _allows_dynamic_information_update(actor_response: str, scene_types: list[str] | None = None) -> bool : Return whether DynamicInformation LLM extraction is warranted.
#   - apply_multi_character_dynamic_information_updates(actor_response: str, pc_id: str, scene_types: list[str] | None, participant_ids: list[str] | None) -> dict[str, dict] : Apply durable DynamicInformation changes for all NPCs in one LLM call.
# ================================

import asyncio
import json
import re
from typing import Any

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.database import async_driver, update_dynamic_information


_DYNAMIC_INFO_SIGNAL_RE = re.compile(
    r"(first sex|first intimacy|virgin|non-virgin|sexual experience|sexual history|"
    r"성경험|첫\s*경험|처음\s*했다|처녀|동정|섹스|관계|성관계|"
    r"성격|personality|became|changed|더\s*차분|더\s*대담|"
    r"키|신장|height|몸무게|체중|weight|살이\s*(?:쪘|빠졌)|cm|kg|"
    r"외모|appearance|평판|reputation|기술|skills|취미|hobby)",
    re.IGNORECASE,
)


_INTIMATE_DURABLE_CHANGE_RE = re.compile(
    r"(intercourse|penetration|lost virginity|virginity loss|pregnan|contraception|"
    r"diagnosed|diagnosis|scar|tattoo|piercing|married|divorced|engaged|"
    r"new job|graduated|moved|relocated)",
    re.IGNORECASE,
)


async def _fetch_dynamic_information(char_id: str) -> dict[str, Any]:
    """현재 DynamicInformation props JSON을 dict로 조회합니다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_INFO]->(n:DynamicInformation)
            RETURN n.props AS props
        """, char_id=char_id)
        row = await rec.single()
    if not row:
        return {}
    try:
        value = json.loads(row["props"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def _fetch_scene_context() -> dict[str, str]:
    """현재 인게임 시간과 위치 이름을 GlobalState에서 조회합니다."""
    async with async_driver.session() as session:
        gs_rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS t, gs.currentLocationId AS loc_id"
        )
        gs_row = await gs_rec.single()

    if not gs_row:
        return {"time": "unknown", "location": "unknown"}

    raw_time = gs_row["t"] or ""
    loc_id = gs_row["loc_id"] or ""

    try:
        from datetime import datetime as _dt
        formatted_time = _dt.fromisoformat(raw_time).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        formatted_time = raw_time or "unknown"

    loc_name = loc_id
    if loc_id:
        async with async_driver.session() as session:
            loc_rec = await session.run(
                "MATCH (l:Location {id: $loc_id}) RETURN l.name AS name",
                loc_id=loc_id,
            )
            loc_row = await loc_rec.single()
        if loc_row and loc_row["name"]:
            loc_name = loc_row["name"]

    return {"time": formatted_time, "location": loc_name or loc_id or "unknown"}


def _needs_dynamic_information_update(actor_response: str) -> bool:
    """느리게 바뀌는 프로필 정보 후보가 있는지 빠르게 판정합니다."""
    return bool(_DYNAMIC_INFO_SIGNAL_RE.search(actor_response))


def _allows_dynamic_information_update(
    actor_response: str,
    scene_types: list[str] | None = None,
) -> bool:
    """Return whether this turn is worth a DynamicInformation LLM pass."""
    if _needs_dynamic_information_update(actor_response):
        return True

    active_scene_types = {str(scene_type).lower() for scene_type in (scene_types or [])}
    if active_scene_types & _INTIMATE_SCENE_TYPES:
        return bool(_INTIMATE_DURABLE_CHANGE_RE.search(actor_response))
    return False


def _sanitize_dynamic_information(
    raw: object,
    allowed_fields: set[str] | None = None,
) -> dict[str, Any]:
    """LLM 결과에서 허용된 DynamicInformation 필드만 남깁니다."""
    if not isinstance(raw, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in raw.items():
        if allowed_fields is not None and key not in allowed_fields:
            continue
        if value in (None, "", [], {}):
            continue
        sanitized[key] = value
    return sanitized


def _field_lines(fields: set[str]) -> str:
    """Format DynamicInformation keys currently present on a node for the prompt."""
    if not fields:
        return "- (none)"
    return "\n".join(f"- {field}" for field in sorted(fields))


async def _run_dynamic_information_update(
    actor_response: str,
    npc_id: str,
    current_info: dict[str, Any],
    scene_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Actor 응답에서 장기 DynamicInformation 변경 후보를 추출합니다."""
    from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

    ctx = scene_context or {}
    scene_time = ctx.get("time", "unknown")
    scene_location = ctx.get("location", "unknown")

    current_sexual_info = current_info.get("sexual_information", "(not set)")
    allowed_fields = set(current_info)

    system_instruction = (
        "You are a conservative DynamicInformation updater for a Korean roleplay system. "
        "Update only durable, explicit facts about the target character."
    )
    prompt = f"""Target character id: {npc_id}
Scene date/time: {scene_time}
Scene location: {scene_location}

=== Current DynamicInformation (full) ===
{json.dumps(current_info, ensure_ascii=False, indent=2)}

=== Current sexual_information (for reference) ===
{current_sexual_info}

Extract slow-changing DynamicInformation updates for the target character only.

Allowed fields are exactly the current DynamicInformation keys:
{_field_lines(allowed_fields)}

Rules:
- Preserve all existing facts; only replace the part that changed. Return the full updated string for any changed field.
- Do not infer hidden changes from arousal, mood, or embarrassment alone. However, virginity loss MUST be recorded if current Experience is "Virgin" and intercourse explicitly occurs in the scene.
- For any field, update only when the scene explicitly changes a durable fact represented by that field.
- Do not update DynamicState fields here.
- Omit unchanged fields.

Return ONLY valid JSON:
{{
  "dynamic_information": {{}}
}}

Scene:
{actor_response[:3000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    _gen_cfg = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }

    raw_text = ""
    for attempt in range(2):
        try:
            response = await model.generate_content_async(prompt, generation_config=_gen_cfg)
        except TimeoutError:
            print(f"[DynamicInformationUpdater] timeout (attempt {attempt + 1})")
            return {}

        raw_text = get_response_text(response)
        if raw_text.strip():
            break

        try:
            finish_reason = response.candidates[0].finish_reason
        except Exception:
            finish_reason = "unknown"
        print(f"[DynamicInformationUpdater] 빈 응답 (attempt {attempt + 1}, finish_reason={finish_reason})")

    if not raw_text.strip():
        return {}

    plan = extract_json_from_llm(raw_text, source="dynamic_information_updater")
    if not isinstance(plan, dict):
        return {}
    return _sanitize_dynamic_information(
        plan.get("dynamic_information"),
        allowed_fields=allowed_fields,
    )


async def _fetch_all_npc_dynamic_info(
    pc_id: str,
    participant_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """PC를 제외한 모든 NPC의 id·name·DynamicInformation props를 조회합니다."""
    allowed_ids = {item for item in (participant_ids or []) if item}
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_INFO]->(n:DynamicInformation)
            RETURN c.id AS char_id, c.name AS char_name, n.props AS props
        """)
        rows = await rec.fetch_all()

    result = []
    for row in rows:
        char_id = row["char_id"]
        if allowed_ids and char_id not in allowed_ids:
            continue
        try:
            info = json.loads(row["props"] or "{}")
        except (TypeError, ValueError):
            info = {}
        if not info:
            print(f"[DynamicInformationUpdater] WARNING: DynamicInformation empty for '{char_id}' — skipping.")
            continue
        result.append({"char_id": char_id, "char_name": row["char_name"], "info": info})
    return result


async def _run_multi_char_dynamic_info_update(
    actor_response: str,
    npcs: list[dict[str, Any]],
    scene_context: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Actor 응답에서 여러 NPC의 DynamicInformation 변경을 한 번의 LLM 호출로 추출합니다."""
    from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

    ctx = scene_context or {}
    scene_time = ctx.get("time", "unknown")
    scene_location = ctx.get("location", "unknown")

    npc_sections = []
    for npc in npcs:
        current_sexual = npc["info"].get("sexual_information", "(not set)")
        npc_sections.append(
            f"[{npc['char_id']} / {npc['char_name']}]\n"
            f"Current DynamicInformation:\n{json.dumps(npc['info'], ensure_ascii=False, indent=2)}\n"
            f"Allowed fields for this character:\n{_field_lines(set(npc['info']))}\n"
            f"Current sexual_information: {current_sexual}"
        )
    npc_block = "\n\n".join(npc_sections)

    system_instruction = (
        "You are a conservative DynamicInformation updater for a Korean roleplay system. "
        "Update only durable, explicit facts. Never invent changes not in the scene."
    )
    prompt = f"""Scene date/time: {scene_time}
Scene location: {scene_location}

=== Character profiles (current DynamicInformation) ===
{npc_block}

For each character listed above, extract slow-changing DynamicInformation updates where an explicit change occurred in the scene.
The PC/player is an in-world character and MUST be updated when explicit durable facts changed.
Omit characters whose information did not change.

Allowed fields are listed separately for each character above.
Do not return fields that are absent from that character's current DynamicInformation.

Rules:
- Return the full updated string (all sub-fields preserved, only changed part replaced) for any changed field.
- Virginity loss MUST be recorded when current Experience is "Virgin" and intercourse explicitly occurs.
- For any field, update only when the scene explicitly changes a durable fact represented by that field.
- Do not infer from arousal, mood, or embarrassment alone.
- Do not update DynamicState fields.
- Omit unchanged characters entirely.

Return ONLY valid JSON:
{{
  "character_updates": [
    {{"char_id": "<id>", "dynamic_information": {{}}}}
  ]
}}

Scene:
{actor_response[:3000]}"""

    model = get_model(model_name=COMPLEX_MODEL, system_prompt=system_instruction)
    _gen_cfg = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }

    raw_text = ""
    for attempt in range(2):
        try:
            response = await model.generate_content_async(prompt, generation_config=_gen_cfg)
        except TimeoutError:
            print(f"[DynamicInformationUpdater] timeout (attempt {attempt + 1})")
            return {}
        raw_text = get_response_text(response)
        if raw_text.strip():
            break
        try:
            finish_reason = response.candidates[0].finish_reason
        except Exception:
            finish_reason = "unknown"
        print(f"[DynamicInformationUpdater] 빈 응답 (attempt {attempt + 1}, finish_reason={finish_reason})")

    if not raw_text.strip():
        return {}

    plan = extract_json_from_llm(raw_text, source="dynamic_information_updater_multi")
    if not isinstance(plan, dict):
        return {}

    allowed_by_char = {npc["char_id"]: set(npc["info"]) for npc in npcs}
    result: dict[str, dict[str, Any]] = {}
    for item in (plan.get("character_updates") or []):
        if not isinstance(item, dict):
            continue
        char_id = item.get("char_id")
        if char_id not in allowed_by_char:
            continue
        updates = _sanitize_dynamic_information(
            item.get("dynamic_information"),
            allowed_fields=allowed_by_char[char_id],
        )
        if updates:
            result[char_id] = updates
    return result


_INTIMATE_SCENE_TYPES = {"intimate", "physical"}


async def apply_multi_character_dynamic_information_updates(
    actor_response: str,
    pc_id: str,
    scene_types: list[str] | None = None,
    participant_ids: list[str] | None = None,
) -> dict[str, dict]:
    """모든 NPC의 DynamicInformation을 씬 기반으로 한 번의 LLM 호출로 업데이트합니다."""
    if not _allows_dynamic_information_update(actor_response, scene_types):
        return {}

    npcs, scene_context = await asyncio.gather(
        _fetch_all_npc_dynamic_info(pc_id, participant_ids=participant_ids),
        _fetch_scene_context(),
    )
    if not npcs:
        print("[DynamicInformationUpdater] no NPC with DynamicInformation found.")
        return {}

    try:
        updates_by_char = await _run_multi_char_dynamic_info_update(actor_response, npcs, scene_context)
    except Exception as exc:
        print(f"[DynamicInformationUpdater] multi-char update failed and ignored: {exc}")
        return {}

    allowed_ids = {npc["char_id"] for npc in npcs}
    updates_by_char = {
        char_id: updates
        for char_id, updates in updates_by_char.items()
        if char_id in allowed_ids
    }

    for char_id, updates in updates_by_char.items():
        await update_dynamic_information(char_id, updates)
        print(f"[DynamicInformationUpdater] applied for {char_id}: {json.dumps(updates, ensure_ascii=False)}")

    return updates_by_char
