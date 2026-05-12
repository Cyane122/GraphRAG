# ================================
# src/agents/prompt_factory/ooc_handler.py
#
# OOC(*...* 마커) 텍스트를 파싱해 세계 상태를 즉각 반영합니다.
#
# Functions
#   - is_ooc(text: str) -> bool : 텍스트에 OOC 마커가 있는지 확인
#   - parse_ooc(text: str, npc_id: str, npc_name: str) -> dict : OOC 분석 후 DB 반영
# ================================

import re
from datetime import datetime, timedelta

from src.config import MODEL_STATE_UPDATER as OOC_MODEL
from src.core.database import update_dynamic_state, move_location, async_driver
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

_BOLD_RE = re.compile(r'\*\*.*?\*\*', re.DOTALL)
_TIME_SET_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_THREE_HOURS_LATER_RE = re.compile(r"3\s*(?:시간|hours?)\s*(?:후|뒤|later)", re.IGNORECASE)
_NEXT_MORNING_RE = re.compile(r"(?:다음\s*날|next\s*day).*(?:아침|morning)", re.IGNORECASE)

_SYSTEM_PROMPT = """\
You are a state extractor for a Korean roleplay system.
The player writes scene directions inside *asterisks*.
Extract world-state changes from the OOC text.

## Available Location IDs
{locations_str}

## Field Rules

time_delta_minutes — minutes to ADD to current time (integer, 0 = no change)
  "3시간 후" → 180 / "다음날" → 1440 / "30분 후" → 30

time_set — set clock to "HH:MM" after applying delta, or null
  "아침" → "08:00" / "새벽" → "05:00" / "저녁" → "18:30" / "자정" → "00:00"
  "곧 동이 터올 것 같다" → "05:00"

location_id — one of the IDs above, or null
  Only set if NPC clearly moves to a different location node.
  Kitchen/bathroom/bedroom = still babe_villa_205.

state_changes — DynamicState fields to update
  Allowed keys: mood, mental_condition, stress_level, physical_condition, injury_detail
  mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
  stress_level: JSON number from 0 to 10 ONLY. Never return strings like "high", "low", or "5".
  physical_condition: healthy/fatigued/injured/ill/hospitalized
  injury_detail: body part + type (e.g. "허리 염좌", "발목 삠")

  IMPORTANT: OOC text establishes current world state.
  Past-tense injury descriptions in OOC = the character IS injured NOW.

  Examples:
  "*잘 자네.*" → no state change
  "*{npc_name}는 좀 화난 듯하다.*" → {"mood": "angry"}
  "*{npc_name}는 스트레스가 심해졌다.*" → {"stress_level": 8}
  "*{npc_name}는 허리를 삐끗했다.*" → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*{npc_name}가 발목을 다쳤다.*" → {"physical_condition": "injured", "injury_detail": "발목 부상"}
  "*{npc_name}는 어젯밤 무거운 걸 옮기다가 허리를 삐끗했다.*" → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*{npc_name}가 열이 난다.*" → {"physical_condition": "ill"}

summary — one-line Korean description of what changed. "변경 없음" if nothing changed.

## Output — return ONLY this JSON, no explanation, no markdown
{
  "time_delta_minutes": 0,
  "time_set": null,
  "location_id": null,
  "state_changes": {},
  "summary": "no change"
}
"""


def is_ooc(text: str) -> bool:
    stripped = _BOLD_RE.sub('', text)
    return '*' in stripped


async def _get_allowed_locations() -> str:
    async with async_driver.session() as session:
        result = await session.run("MATCH (l:Location) RETURN l.id AS id, l.name AS name")
        records = await result.data()
        locations = [f'- "{rec["id"]}" ({rec["name"]})' for rec in records]
        return "\n".join(locations) if locations else "- No registered locations."


def _coerce_delta_minutes(value: object) -> int:
    """OOC time_delta_minutes 출력을 안전한 분 단위 정수로 변환한다."""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return 0
    return minutes if 0 <= minutes < 10080 else 0


def _parse_current_time(raw: object) -> datetime:
    """GlobalState.currentTime 값을 datetime으로 안전하게 정규화합니다."""
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now()


def _augment_time_plan_from_text(text: str, plan: dict) -> dict:
    """LLM이 흔한 시간 표현을 누락했을 때 deterministic rule로 보완합니다."""
    plan = dict(plan)
    if not plan.get("time_delta_minutes") and _THREE_HOURS_LATER_RE.search(text):
        plan["time_delta_minutes"] = 180
    if not plan.get("time_set") and _NEXT_MORNING_RE.search(text):
        plan["time_delta_minutes"] = max(_coerce_delta_minutes(plan.get("time_delta_minutes")), 1440)
        plan["time_set"] = "08:00"
    return plan


async def _location_exists(location_id: str) -> bool:
    """OOC가 반환한 location_id가 실제 Location인지 확인한다."""
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (l:Location {id: $loc_id}) RETURN l.id AS id",
            loc_id=location_id,
        )
        return await result.single() is not None


async def _apply_time_change(delta_minutes: object, time_set: object) -> dict:
    """OOC가 요청한 시간 이동을 GlobalState.currentTime에 반영한다."""
    delta = _coerce_delta_minutes(delta_minutes)
    match = _TIME_SET_RE.match(str(time_set or ""))
    if delta <= 0 and not match:
        return {
            "time_changed": False,
            "time_before": None,
            "time_after": None,
            "applied_time_delta_minutes": 0,
            "applied_time_set": None,
        }

    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS current_time"
        )
        row = await result.single()
        current_raw = row["current_time"] if row else None
        current_time = _parse_current_time(current_raw)

        new_time = current_time + timedelta(minutes=delta)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            target = new_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if delta == 0 and target <= current_time:
                target += timedelta(days=1)
            new_time = target

        # KuzuDB SET + $param 버그 우회 — time_plan.py와 동일한 리터럴 삽입 방식 사용
        _safe_iso = new_time.isoformat().replace("\\", "\\\\").replace("'", "\\'")
        await session.run(
            f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentTime = '{_safe_iso}'"
        )
    return {
        "time_changed": True,
        "time_before": current_time.isoformat(),
        "time_after": new_time.isoformat(),
        "applied_time_delta_minutes": delta,
        "applied_time_set": f"{match.group(1).zfill(2)}:{match.group(2)}" if match else None,
    }


async def parse_ooc(text: str, npc_id: str, npc_name: str) -> dict:
    """OOC 텍스트를 분석하고 DB에 즉각 반영합니다 (비동기)"""

    locations = await _get_allowed_locations()
    system_prompt = _SYSTEM_PROMPT \
        .replace("{locations_str}", locations) \
        .replace("{npc_name}", npc_name)

    model = get_model(model_name=OOC_MODEL, system_prompt=system_prompt)

    response = await model.generate_content_async(
        text,
        generation_config={"max_output_tokens": 1024, "temperature": 0.0, "thinking_config": {"thinking_level": "LOW"}, "response_mime_type": "application/json"},
    )

    plan = extract_json_from_llm(get_response_text(response), source="ooc_parser")
    if not plan:
        plan = {"state_changes": {}, "summary": "parse failed"}
    plan = _augment_time_plan_from_text(text, plan)

    state_changes = plan.get("state_changes", {})
    if state_changes:
        await update_dynamic_state(npc_id, state_changes)

    new_location = plan.get("location_id")
    if new_location and await _location_exists(new_location):
        await move_location(npc_id, new_location)
    else:
        new_location = None

    time_result = await _apply_time_change(
        plan.get("time_delta_minutes"),
        plan.get("time_set"),
    )
    time_changed = time_result["time_changed"]

    summary = plan.get("summary", "상태 변경 없음")

    if state_changes or new_location or time_changed:
        print(f"[OOC / {OOC_MODEL}] {summary}")

    return {
        "state_changes": state_changes,
        "time_changed": time_changed,
        "time_before": time_result["time_before"],
        "time_after": time_result["time_after"],
        "applied_time_delta_minutes": time_result["applied_time_delta_minutes"],
        "applied_time_set": time_result["applied_time_set"],
        "summary": summary,
    }
