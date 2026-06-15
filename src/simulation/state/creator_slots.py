# ================================
# src/simulation/state/creator_slots.py
#
# World schema가 선언한 커스텀 슬롯의 느리게 변하는 활동 상태를 갱신합니다.
#
# Functions
#   - has_dynamic_slot_signal(actor_response: str, world_config: dict | None = None) -> bool : 동적 슬롯 갱신 신호 여부를 반환합니다.
#   - apply_creator_slot_updates(actor_response: str, participant_ids: list[str] | None = None, world_config: dict | None = None) -> dict[str, dict[str, dict]] : 설정된 커스텀 슬롯 업데이트를 추출하고 저장합니다.
# ================================

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from src.config import MODEL_COMPLEX_UPDATER as COMPLEX_MODEL
from src.core.database import async_driver


_DEFAULT_TRIGGERS: tuple[str, ...] = (
    "유튜브", "youtube", "채널", "구독자", "조회수", "댓글", "업로드", "영상",
    "노래", "곡", "불렀", "부른", "부르", "녹음", "보컬", "vocal", "sing", "song",
)


def _slot_configs(world_config: dict | None) -> list[dict[str, Any]]:
    """world_config에서 동적 커스텀 슬롯 설정 목록을 반환합니다."""
    configs = (world_config or {}).get("dynamic_slot_updaters") or []
    return [dict(item) for item in configs if isinstance(item, dict) and item.get("label")]


def _trigger_pattern(configs: list[dict[str, Any]]) -> re.Pattern[str]:
    """슬롯 설정의 trigger 문자열을 하나의 정규식으로 컴파일합니다."""
    words: list[str] = []
    for config in configs:
        words.extend(str(item) for item in (config.get("triggers") or []) if str(item).strip())
    if not words:
        words.extend(_DEFAULT_TRIGGERS)
    return re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE)


def has_dynamic_slot_signal(actor_response: str, world_config: dict | None = None) -> bool:
    """동적 슬롯 갱신 신호 여부를 반환합니다."""
    configs = _slot_configs(world_config)
    return bool(configs and _trigger_pattern(configs).search(actor_response or ""))


def _safe_json_dict(raw: object) -> dict[str, Any]:
    """props JSON 문자열을 dict로 파싱합니다."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _coerce_int(value: object) -> int | None:
    """쉼표 포함 문자열 등에서 안전하게 양의 정수를 추출합니다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    text = re.sub(r"[^0-9]", "", str(value or ""))
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _with_numeric_aliases(slot: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """설정된 숫자 필드의 legacy 문자열 alias를 보강한 복사본을 반환합니다."""
    enriched = dict(slot)
    alias_map = config.get("numeric_aliases") or {}
    for target, source in alias_map.items():
        if target in enriched:
            continue
        count = _coerce_int(enriched.get(source))
        if count is not None:
            enriched[str(target)] = count
    if config.get("label") == "Youtube":
        if "subscriber_count" not in enriched:
            count = _coerce_int(enriched.get("subscribers"))
            if count is not None:
                enriched["subscriber_count"] = count
        if "total_views" not in enriched:
            views = _coerce_int(enriched.get("total_view"))
            if views is not None:
                enriched["total_views"] = views
    return enriched


async def _fetch_slot_rows(
    config: dict[str, Any],
    participant_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """특정 커스텀 슬롯을 가진 참여 캐릭터와 props를 조회합니다."""
    label = str(config.get("label") or "")
    if not label.isidentifier():
        return []
    allowed_ids = {item for item in (participant_ids or []) if item}
    rel_name = f"HAS_{label.upper()}"
    try:
        async with async_driver.session() as session:
            rec = await session.run(
                f"""
                MATCH (c:Character)-[:{rel_name}]->(n:{label})
                RETURN c.id AS char_id, c.name AS char_name, n.id AS node_id, n.props AS props
                """
            )
            rows = await rec.fetch_all()
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        char_id = str(row["char_id"] or "")
        if allowed_ids and char_id not in allowed_ids:
            continue
        props = _with_numeric_aliases(_safe_json_dict(row["props"]), config)
        if props:
            result.append({
                "slot": str(config.get("slot") or label),
                "label": label,
                "char_id": char_id,
                "char_name": row["char_name"],
                "node_id": row["node_id"],
                "props": props,
                "config": config,
            })
    return result


async def _fetch_dynamic_slots(
    configs: list[dict[str, Any]],
    participant_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """설정된 동적 슬롯 보유 캐릭터 목록을 조회합니다."""
    groups = await asyncio.gather(*(_fetch_slot_rows(config, participant_ids) for config in configs))
    return [row for group in groups for row in group]


def _allowed_fields(config: dict[str, Any], current: dict[str, Any]) -> set[str]:
    """슬롯별 업데이트 허용 필드 집합을 반환합니다."""
    return set(current) | {str(field) for field in (config.get("allowed_fields") or [])}


def _list_limits(config: dict[str, Any]) -> dict[str, int]:
    """설정의 리스트 필드 길이 제한을 반환합니다."""
    raw = config.get("list_fields") or {}
    if isinstance(raw, list):
        return {str(field): 20 for field in raw}
    if not isinstance(raw, dict):
        return {}
    limits: dict[str, int] = {}
    for field, limit in raw.items():
        try:
            limits[str(field)] = max(1, int(limit))
        except (TypeError, ValueError):
            limits[str(field)] = 20
    return limits


def _trim_list(field: str, value: object, limits: dict[str, int]) -> list | None:
    """리스트 필드 길이를 제한하고 비어 있으면 None을 반환합니다."""
    if not isinstance(value, list):
        return None
    items = [item for item in value if item not in (None, "", [], {})]
    limit = limits.get(field, 20)
    return items[-limit:] if items else None


def _sanitize_slot_updates(
    config: dict[str, Any],
    raw: object,
    current: dict[str, Any],
) -> dict[str, Any]:
    """LLM 슬롯 업데이트를 허용 필드와 타입 제약으로 정리합니다."""
    if not isinstance(raw, dict):
        return {}
    allowed = _allowed_fields(config, current)
    numeric_fields = {str(field) for field in (config.get("numeric_fields") or [])}
    list_limits = _list_limits(config)
    sanitized: dict[str, Any] = {}
    for field, value in raw.items():
        field = str(field)
        if field not in allowed or value in (None, "", [], {}):
            continue
        if field in numeric_fields:
            int_value = _coerce_int(value)
            if int_value is not None:
                sanitized[field] = int_value
            continue
        if field in list_limits:
            list_value = _trim_list(field, value, list_limits)
            if list_value:
                sanitized[field] = list_value
            continue
        sanitized[field] = value
    return {
        field: value
        for field, value in sanitized.items()
        if current.get(field) != value
    }


async def _run_dynamic_slot_update(
    actor_response: str,
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Actor 응답에서 설정된 커스텀 슬롯 변경 후보를 추출합니다."""
    from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

    slot_blocks = []
    for row in rows:
        config = row["config"]
        slot_blocks.append(
            f"[{row['char_id']} / {row['char_name']} / {row['slot']}]\n"
            f"Current props:\n{json.dumps(row['props'], ensure_ascii=False, indent=2)}\n"
            f"Allowed fields:\n{', '.join(sorted(_allowed_fields(config, row['props'])))}\n"
            f"Slot instructions: {config.get('instructions') or '(none)'}"
        )
    slot_context = "\n\n".join(slot_blocks)

    prompt = f"""Extract conservative custom-slot updates from an accepted Korean roleplay scene.

Targets:
{slot_context}

Rules:
- Preserve existing facts. Return only changed fields.
- Update a slot only when the accepted scene explicitly changes facts covered by that slot's instructions.
- Do not invent exact numbers. Numeric fields may change only if the scene gives a number or clearly states a small/large growth or drop.
- For vague growth/drop, use conservative increments appropriate to the described scale.
- Keep list entries compact dicts with title/artist/context/date_or_time/reaction when known.
- Omit unchanged characters and unchanged slots.

Return ONLY valid JSON:
{{
  "character_updates": [
    {{
      "char_id": "<id>",
      "slots": {{
        "<slot>": {{}}
      }}
    }}
  ]
}}

Scene:
{actor_response[:3500]}"""

    model = get_model(
        model_name=COMPLEX_MODEL,
        system_prompt="Conservative custom slot updater. Never invent unsupported metrics.",
    )
    generation_config = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
        "log_source": "dynamic_slot_updater",
        "bypass_safety": True,
    }

    raw_text = ""
    for attempt in range(2):
        try:
            response = await model.generate_content_async(prompt, generation_config=generation_config)
        except TimeoutError:
            print(f"[DynamicSlotUpdater] timeout (attempt {attempt + 1})")
            return {}
        raw_text = get_response_text(response)
        if raw_text.strip():
            break
        try:
            finish_reason = response.candidates[0].finish_reason
        except Exception:
            finish_reason = "unknown"
        print(f"[DynamicSlotUpdater] empty response (attempt {attempt + 1}, finish_reason={finish_reason})")

    if not raw_text.strip():
        return {}

    plan = extract_json_from_llm(raw_text, source="dynamic_slot_updater")
    if not isinstance(plan, dict):
        return {}

    current_by_char_slot = {
        (row["char_id"], row["slot"]): (row["config"], row["props"])
        for row in rows
    }
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for item in plan.get("character_updates") or []:
        if not isinstance(item, dict):
            continue
        char_id = item.get("char_id")
        slots = item.get("slots")
        if not isinstance(char_id, str) or not isinstance(slots, dict):
            continue
        for slot, raw_updates in slots.items():
            current_entry = current_by_char_slot.get((char_id, str(slot)))
            if current_entry is None:
                continue
            config, current = current_entry
            updates = _sanitize_slot_updates(config, raw_updates, current)
            if updates:
                result.setdefault(char_id, {})[str(slot)] = updates
    return result


async def _save_slot_props(label: str, node_id: str, props: dict[str, Any]) -> None:
    """커스텀 슬롯 props JSON을 저장합니다."""
    if not label.isidentifier():
        return
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (n:{label} {{id: $node_id}}) SET n.props = $props",
            node_id=node_id,
            props=json.dumps(props, ensure_ascii=False),
        )


async def apply_creator_slot_updates(
    actor_response: str,
    participant_ids: list[str] | None = None,
    world_config: dict | None = None,
) -> dict[str, dict[str, dict]]:
    """설정된 커스텀 슬롯 업데이트를 추출하고 저장합니다."""
    configs = _slot_configs(world_config)
    if not configs or not has_dynamic_slot_signal(actor_response, world_config):
        return {}

    rows = await _fetch_dynamic_slots(configs, participant_ids)
    if not rows:
        return {}

    try:
        updates_by_char = await _run_dynamic_slot_update(actor_response, rows)
    except Exception as exc:
        print(f"[DynamicSlotUpdater] update failed and ignored: {exc}")
        return {}

    rows_by_char_slot = {
        (row["char_id"], row["slot"]): row
        for row in rows
    }
    applied: dict[str, dict[str, dict]] = {}
    for char_id, slot_updates in updates_by_char.items():
        for slot, updates in slot_updates.items():
            row = rows_by_char_slot.get((char_id, slot))
            if row is None:
                continue
            merged = dict(row["props"])
            merged.update(updates)
            await _save_slot_props(row["label"], row["node_id"], merged)
            applied.setdefault(char_id, {})[slot] = updates
            print(f"[DynamicSlotUpdater] applied for {char_id}/{slot}: {json.dumps(updates, ensure_ascii=False)}")

    return applied
