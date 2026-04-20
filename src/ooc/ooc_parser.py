# src/ooc/ooc_parser.py
"""
OOC (Out of Character) parser.
Detects *...* markers and extracts world-state changes via LLM.
"""

import re
import json
import os
import anthropic
from datetime import datetime, timedelta
from neo4j import GraphDatabase
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")

client = anthropic.Anthropic()
OOC_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

_BOLD_RE = re.compile(r'\*\*.*?\*\*', re.DOTALL)

LOCATIONS = {
    "babe_villa_205": "바베빌라 205호 (은서+시안의 집, 방, 주방, 욕실 포함)",
    "babe_univ_gym":  "바베대학교 헬스장 (은서 근무지, 월/금 16:00-23:00)",
}

_SYSTEM_PROMPT = """\
You are a state extractor for a Korean roleplay system.
The player writes scene directions inside *asterisks*.
Extract world-state changes from the OOC text.

## Available Location IDs
LOCATIONS_PLACEHOLDER

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
  "*잘 자네." → no state change
  "*은서 기분: 화남" → {"mood": "angry"}
  "*은서는 허리를 삐끗했다." → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*은서가 발목을 다쳤다." → {"physical_condition": "injured", "injury_detail": "발목 부상"}
  "*은서는 어젯밤 무거운 걸 옮기다가 허리를 삐끗했다." → {"physical_condition": "injured", "injury_detail": "허리 염좌"}
  "*은서가 열이 난다." → {"physical_condition": "ill"}

summary — one-line Korean description of what changed. "변경 없음" if nothing changed.
"""


def is_ooc(text: str) -> bool:
    stripped = _BOLD_RE.sub('', text)
    return '*' in stripped


def parse_ooc(text: str, current_dt: datetime, npc_id: str) -> dict:
    locations_str = "\n".join(f'  "{k}": "{v}"' for k, v in LOCATIONS.items())
    system = _SYSTEM_PROMPT.replace("LOCATIONS_PLACEHOLDER", locations_str)

    response = client.messages.create(
        model=OOC_MODEL,
        max_tokens=256,
        temperature=0.0,
        system=system,
        messages=[{"role": "user", "content": text}],
    )

    raw = response.content[0].text.strip()
    try:
        import re as _re
        start = raw.find('{')
        end   = raw.rfind('}')
        if start == -1 or end == -1:
            raise json.JSONDecodeError("no JSON object found", raw, 0)
        json_str = _re.sub(r',\s*([}\]])', r'\1', raw[start:end + 1])
        plan: dict = json.loads(json_str)
    except json.JSONDecodeError:
        print(f"[OOC] parse failed: {raw[:100]}")
        plan = {"time_delta_minutes": 0, "time_set": None,
                "location_id": None, "state_changes": {}, "summary": "parse failed"}

    # Apply time delta
    new_dt = current_dt
    delta_min = plan.get("time_delta_minutes", 0)
    if delta_min:
        new_dt = new_dt + timedelta(minutes=int(delta_min))

    time_set = plan.get("time_set")
    if time_set:
        try:
            h, m = map(int, time_set.split(":"))
            new_dt = new_dt.replace(hour=h, minute=m, second=0)
        except ValueError:
            pass

    # Advance cycle_day by elapsed days
    day_delta = (new_dt.date() - current_dt.date()).days
    if day_delta > 0:
        _advance_cycle_day(npc_id, day_delta)

    # Location change
    new_location = plan.get("location_id")
    if new_location:
        _move_location(npc_id, new_location)

    # State changes
    state_changes = plan.get("state_changes", {})
    if state_changes:
        _update_state(npc_id, state_changes)

    summary = plan.get("summary", "변경 없음")
    print(f"[OOC / {OOC_MODEL}] {summary}")

    return {
        "new_dt":        new_dt,
        "new_location":  new_location,
        "state_changes": state_changes,
        "summary":       summary,
    }


# ── DB helpers ────────────────────────────────────────────

def _advance_cycle_day(char_id: str, days: int) -> None:
    with driver.session() as session:
        session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            SET d.cycle_day = ((d.cycle_day + $days - 1) % 28) + 1
        """, char_id=char_id, days=days)


def _move_location(char_id: str, new_loc_id: str) -> None:
    with driver.session() as session:
        session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(prev:Location)
            DELETE old
            SET prev.current_chars = [x IN prev.current_chars WHERE x <> $char_id]
        """, char_id=char_id)
        session.run("""
            MATCH (c:Character {id: $char_id})
            MATCH (next:Location {id: $new_loc_id})
            CREATE (c)-[:LOCATED_AT]->(next)
            SET next.current_chars = coalesce(next.current_chars, []) + [$char_id]
        """, char_id=char_id, new_loc_id=new_loc_id)
        session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            SET d.location_id = $new_loc_id
        """, char_id=char_id, new_loc_id=new_loc_id)


def _update_state(char_id: str, updates: dict) -> None:
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    with driver.session() as session:
        session.run(f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            SET {set_clause}
        """, char_id=char_id, **updates)