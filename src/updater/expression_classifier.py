"""
LITERAL/FIGURATIVE 분류기.
Actor 응답 텍스트에서 DynamicState 변경 필드를 추출한다.
outfit(현재 의상)과 injury_marks(가시적 부상 흔적)를 포함해
묘사 일관성 버퍼를 지원한다.
"""

import os

from src.utils.llm_utils import async_llm_client, extract_json_from_llm

CLASSIFIER_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

SAFE_FIELDS = {
    "mood", "mental_condition", "stress_level",
    "workplace_stress_level", "cycle_day", "location_id", "affinity",
    "outfit", "injury_marks",
}
PHYSICAL_FIELDS = {"physical_condition", "injury_detail"}


async def classify_and_extract(actor_response: str) -> dict:
    prompt = f"""You are a state extractor for a roleplay system.
Read the roleplay text and extract meaningful state changes for the NPC.

## Classification
LITERAL: Direct physical events — injury, illness, confirmed physical state, clothing description
  "팔을 다쳤어" / "발목을 삐었다" / "열이 38도야" / rubbing injured area at a clinic
  "코트를 걸쳤다" / "민소매 차림이었다" / "잠옷 바지를 입은 채"
FIGURATIVE: Emotional or metaphorical — never update physical fields
  "심장이 터질 것 같아" / "죽고 싶다" / "온몸이 녹아내리는 것 같아"

## Allowed output fields

### Always extractable
- mood: calm/happy/sad/angry/anxious/tired/annoyed/excited
- mental_condition: stable/stressed/anxious/depressed/exhausted
- stress_level: 0–10 integer
- workplace_stress_level: 0–10 integer
- outfit: current clothing description IF explicitly mentioned or visibly changed.
    Only update when clothing is directly described in narration.
    Keep concise (≤25 chars Korean). Examples:
    → "청바지 + 흰 니트" / "운동복 차림" / "수면 반바지 + 민소매"
    → Do NOT update if clothing is not mentioned at all.
- injury_marks: "없음" or short description of VISIBLE injury marks on the body.
    Only update if an injury is described or healed this scene.
    Examples: "오른 발목 부상" / "팔 찰과상" / "허리 염좌" / "없음"

### LITERAL only
- physical_condition: healthy/fatigued/injured/ill/hospitalized
- injury_detail: string (body part + type) — LITERAL only

## Rules
"심장이 부서질 것 같아" → FIGURATIVE → mental_condition only, no physical
"발목을 다쳤다" → LITERAL → physical_condition: injured, injury_detail: "발목 부상", injury_marks: "오른 발목 부상"
"파자마 바지를 입은 채로 소파에 앉아" → outfit: "파자마 바지"
"허리를 누르며 병원에서 통증 언급" → LITERAL → physical_condition: injured, injury_detail: "허리 염좌", injury_marks: "허리 염좌"
Only extract fields that ACTUALLY CHANGED in this scene. Omit unchanged fields entirely.

## Scale Calibration
### stress_level (General life stress)
- 10: Family member gravely injured / Partner's infidelity / Funeral
-  8: Major fight risking breakup / Fired from job
-  5: Failed an important exam / Serious argument with a friend
-  3: Annoying group project / Lost wallet / Minor disagreement
-  1: Minor daily hassle (e.g., paper cut, spilled coffee)
-  0: A perfect, peaceful day

### workplace_stress_level
- 10: Fired / Major public humiliation / Facing academic expulsion
-  8: Severe harassment from a client/boss / Failed a major project
-  6: Constant lingering stares or uncomfortable touches from clients
-  4: Annoying team member causing repeated rework
-  2: A single rude customer / Difficult but manageable task
-  0: Smooth and uneventful shift

Return ONLY a JSON object with changed fields.
Empty object {{}} if nothing meaningful changed.
No explanation, no markdown.

Roleplay text:
{actor_response[:1500]}"""

    response = await async_llm_client.messages.create(
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