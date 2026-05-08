# ================================
# src/simulation/state/classifier.py
#
# Actor 응답 텍스트에서 상태 변화 필드를 분류·추출합니다.
# LITERAL(물리적 사건) / FIGURATIVE(감정·비유) 분류 후 DynamicState 변경 목록 반환.
#
# Functions
#   - classify_and_extract(actor_response: str) -> dict : Actor 응답에서 DynamicState 변경 필드 추출
#   - _sanitize_stress_level(value) -> int | None : stress_level 다양한 입력값을 정수로 변환
# ================================

from src.config import MODEL_STATE_UPDATER as CLASSIFIER_MODEL
from src.core.llm.client import get_model, extract_json_from_llm

SAFE_FIELDS = {
    "mood", "mental_condition", "stress_level",
    "workplace_stress_level", "cycle_day", "location_id",
    "outfit", "injury_marks",
}
PHYSICAL_FIELDS = {"physical_condition", "injury_detail"}

def _sanitize_stress_level(value=None) -> int | None:
    """
    LLM이 stress_level에 대해 반환할 수 있는 다양한 값을 정수로 변환합니다.
    - 정수이면 그대로 반환
    - "5" 같은 숫자 문자열이면 정수로 변환
    - "low", "medium", "high" 같은 문자열이면 미리 정의된 값으로 매핑
    - 그 외의 경우는 None을 반환하여 해당 필드를 무시하도록 함
    """
    if isinstance(value, int) and 0 <= value <= 10:
        return value
    if isinstance(value, str):
        try:
            num_val = int(value)
            if 0 <= num_val <= 10:
                return num_val
        except (ValueError, TypeError):
            mapping = {
                "none": 0, "very low": 1, "low": 2,
                "medium-low": 4, "medium": 5, "mid": 5,
                "medium-high": 6, "high": 8, "very high": 9, "max": 10
            }
            return mapping.get(value.lower().strip())
    return None

async def classify_and_extract(actor_response: str) -> dict:
    """Actor 응답 텍스트를 분석해 변경된 DynamicState 필드를 dict로 반환한다."""
    system_instruction = """You are a state extractor for a roleplay system.
Read the roleplay text and extract meaningful state changes for the NPC."""

    prompt = f"""## Classification
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

    model = get_model(model_name=CLASSIFIER_MODEL, system_prompt=system_instruction)

    response = await model.generate_content_async(
        prompt,
        generation_config={"temperature": 0.0, "max_output_tokens": 1024, "thinking_config": {"thinking_level": "LOW"}, "response_mime_type": "application/json"}
    )

    changes = extract_json_from_llm(response.text, source="expression_classifier")

    safe_changes = {}
    for field, value in changes.items():
        if field in {"stress_level", "workplace_stress_level"}:
            sanitized_value = _sanitize_stress_level(value)
            if sanitized_value is not None:
                safe_changes[field] = sanitized_value
            continue
        if field in SAFE_FIELDS:
            safe_changes[field] = value
        elif field in PHYSICAL_FIELDS:
            safe_changes[field] = value
            print(f"[LITERAL] {field} = {value}")

    if safe_changes:
        print(f"[Classifier] extracted: {safe_changes}")

    return safe_changes
