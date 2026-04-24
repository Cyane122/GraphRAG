"""
OOC (Out of Character) parser.
Detects *...* markers and extracts world-state changes via LLM.
"""

import re
import os
from dotenv import load_dotenv
from pathlib import Path

from src.utils.db_utils import update_dynamic_state, move_location, async_driver
from src.utils.llm_utils import extract_json_from_llm, llm_client

load_dotenv(Path(__file__).parent.parent.parent / ".env")

OOC_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

_BOLD_RE = re.compile(r'\*\*.*?\*\*', re.DOTALL)

_SYSTEM_PROMPT = """\
You are a state extractor for a Korean roleplay system.
The player writes scene directions inside *asterisks*.
Extract world-state changes from the OOC text.

## Available Location IDs
{locations_str}

## Output — return ONLY this JSON, no explanation, no markdown
{
  "time_delta_minutes": 0,
  "time_set": null,
  "location_id": null,
  "state_changes": {},
  "summary": "no change"
}

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
  physical_condition: healthy/fatigued/injured/ill/hospitalized
  injury_detail: body part + type (e.g. "허리 염좌", "발목 삠")

  IMPORTANT: OOC text establishes current world state.
  Past-tense injury descriptions in OOC = the character IS injured NOW.

  Examples:
  "*잘 자네.*" → no state change
  "*{npc_name}는 좀 화난 듯하다.*" → {"mood": "angry"}
  "*{npc_name}는 허리를 삐끗했다.*" → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*{npc_name}가 발목을 다쳤다.*" → {"physical_condition": "injured", "injury_detail": "발목 부상"}
  "*{npc_name}는 어젯밤 무거운 걸 옮기다가 허리를 삐끗했다.*" → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*{npc_name}가 열이 난다.*" → {"physical_condition": "ill"}

summary — one-line Korean description of what changed. "변경 없음" if nothing changed.
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


async def parse_ooc(text: str, npc_id: str, npc_name: str) -> dict:
    """OOC 텍스트를 분석하고 DB에 즉각 반영합니다 (비동기)"""

    locations = await _get_allowed_locations()
    system_prompt = _SYSTEM_PROMPT \
        .replace("{locations+_str}", locations) \
        .replace("{npc}", npc_name)
    # 1. LLM API 호출
    response = llm_client.messages.create(
        model=OOC_MODEL,
        max_tokens=256,
        temperature=0.0,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    # 2. 공통 유틸리티로 안전하게 JSON 파싱
    plan = extract_json_from_llm(response.content[0].text)
    if not plan:
        plan = {"state_changes": {}, "summary": "parse failed"}

    # 3. 상태 변화(DynamicState) DB 반영
    state_changes = plan.get("state_changes", {})
    if state_changes:
        await update_dynamic_state(npc_id, state_changes)

    # 4. 장소 이동(Location) DB 반영 (기존에 프롬프트엔 있었으나 누락되었던 로직 추가)
    new_location = plan.get("location_id")
    if new_location:
        await move_location(npc_id, new_location)

    summary = plan.get("summary", "상태 변경 없음")

    # 상태나 장소 변화가 있었을 때만 콘솔에 로깅
    if state_changes or new_location:
        print(f"[OOC / {OOC_MODEL}] {summary}")

    return {
        "state_changes": state_changes,
        "summary": summary,
    }