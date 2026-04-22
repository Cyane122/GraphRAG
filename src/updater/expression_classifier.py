# src/updater/expression_classifier.py
"""
Classifies expressions in Actor output as Literal or Figurative,
then extracts DynamicState field updates accordingly.
"""

import os

# 공통 유틸리티 Import
from src.utils.llm_utils import llm_client, extract_json_from_llm

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

## Scale Calibration (CRITICAL)
Use these examples to calibrate the 0-10 integer scale. Do NOT overestimate.

### stress_level (General life stress)
- 10: Family member gravely injured / Partner's infidelity / Funeral
-  8: Major fight risking breakup / Fired from job
-  5: Failed an important exam / Serious argument with a friend
-  3: Annoying group project / Lost wallet / Minor disagreement
-  1: Minor daily hassle (e.g., paper cut, spilled coffee)
-  0: A perfect, peaceful day

### workplace_stress_level (Stress from job/school)
- 10: Fired / Major public humiliation at work / Facing academic expulsion
-  8: Severe harassment from a client/boss / Failed a major project
-  6: Constant lingering stares or uncomfortable touches from clients
-  4: Annoying team member causing repeated rework / A single, particularly rude client who crossed a line but was manageable
-  2: A single rude customer / Difficult but manageable task
-  0: Smooth and uneventful shift

Return ONLY a JSON object with changed fields. Empty object {{}} if nothing meaningful changed.
No explanation, no markdown.

Roleplay text:
{actor_response[:1500]}"""

    response = llm_client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=256,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )

    changes = extract_json_from_llm(response.content[0].text)

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