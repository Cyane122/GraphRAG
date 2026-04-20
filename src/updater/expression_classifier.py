# src/updater/expression_classifier.py
"""
Classifies expressions in Actor output as Literal or Figurative,
then extracts DynamicState field updates accordingly.
"""

import json
import os
import anthropic
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / ".env")

client = anthropic.Anthropic()
CLASSIFIER_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

SAFE_FIELDS = {
    "mood", "mental_condition", "stress_level",
    "workplace_stress_level", "cycle_day", "location_id", "affinity",
}
PHYSICAL_FIELDS = {"physical_condition", "injury_detail"}


def classify_and_extract(actor_response: str) -> dict:
    prompt = f"""You are a state extractor for a roleplay system.
Read the roleplay text and extract meaningful state changes for the NPC.

## Classification
LITERAL: Direct physical events — injury, illness, confirmed physical state
  "팔을 다쳤어" / "발목을 삐었다" / "열이 38도야" / rubbing injured area at a clinic
FIGURATIVE: Emotional or metaphorical — never update physical fields
  "심장이 터질 것 같아" / "죽고 싶다" / "온몸이 녹아내리는 것 같아"

## Extract BOTH new events AND established physical states
If the scene shows a character already injured (at a hospital, pressing a sore area,
describing pain), extract that as current physical state even if it happened earlier.

## Allowed output fields
- mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
- mental_condition: stable/stressed/anxious/depressed/exhausted
- stress_level: 0–10 integer
- workplace_stress_level: 0–10 integer
- physical_condition: healthy/fatigued/injured/ill/hospitalized — LITERAL only
- injury_detail: string (body part + type) — LITERAL only

## Rules
"심장이 부서질 것 같아" → FIGURATIVE → mental_condition only
"죽고 싶다" → FIGURATIVE → mental_condition: depressed
"발목을 다쳤다" → LITERAL → physical_condition: injured, injury_detail: "발목 부상"
"허리를 누르며 / 병원에서 허리 통증 언급" → LITERAL → physical_condition: injured, injury_detail: "허리 염좌"
Hospital/clinic scene + pressing body part + describing pain → LITERAL

Return ONLY a JSON object with changed fields. Empty object {{}} if nothing meaningful changed.
No explanation, no markdown.

Roleplay text:
{actor_response[:1500]}"""

    response = client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=256,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    try:
        import re as _re
        start = raw.find('{')
        end   = raw.rfind('}')
        if start == -1 or end == -1:
            raise json.JSONDecodeError("no JSON object found", raw, 0)
        json_str = _re.sub(r',\s*([}\]])', r'\1', raw[start:end + 1])
        changes: dict = json.loads(json_str)
        if not isinstance(changes, dict):
            changes = {}
    except json.JSONDecodeError:
        print(f"[Classifier] parse failed: {raw[:100]}")
        changes = {}

    safe_changes = {}
    for field, value in changes.items():
        if field in SAFE_FIELDS:
            safe_changes[field] = value
        elif field in PHYSICAL_FIELDS:
            safe_changes[field] = value
            print(f"[LITERAL] {field} = {value}")

    if safe_changes:
        print(f"[Classifier] extracted: {safe_changes}")

    return safe_changes