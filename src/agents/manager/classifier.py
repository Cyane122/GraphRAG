# ================================
# src/agents/manager/classifier.py
#
# Scene classification and time parsing for the Manager pipeline.
#
# Functions
#   - _try_rule_based(user_input: str) -> dict | None : Fast-path classification for short inputs
#   - _classify_and_parse_time(user_input: str, recent_story: str, global_state: dict, allowed_locs: str, scene_descriptions: dict[str, str] | None = None) -> dict : LLM-based scene and time parsing
# ================================
import asyncio
import re
from datetime import datetime

from src.config import MODEL_CLASSIFIER as CLASSIFIER_MODEL
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

_NEEDS_LLM_PATTERN = re.compile(
    r"\*|다음\s*날|내일|어제|시간\s*후|분\s*후|나중에|며칠|다음\s*주|"
    r"이동|장소|헬스장|카페|학교|편의점|집에|나갔|들어왔|"
    r"날씨|비|눈|천둥|intimate|직장|workplace"
)
# user_input(OOC 포함)에서 intimate 키워드 감지 시 바로 반환
_INTIMATE_INPUT_PATTERN = re.compile(
    r"자지|보지|섹스|성관계|삽입|신음|절정|쾌감|흥분|발기|애무|"
    r"빨아|핥아|핥어|핥|만져줘|만지고|넣어|박아|박히|느껴|몸속|오르가슴|클라이막스|"
    r"팬티|브래지어|속옷|맨살|알몸|나체"
)
# intimate 씬이 진행 중인지 recent_story에서 감지 (명시적 성적 행위 + 탈의/노출 빌드업 포함)
_INTIMATE_CONTEXT_PATTERN = re.compile(
    r"자지|보지|섹스|성관계|발기|애무|빨아|핥|만져|넣어|박아|박히|"
    r"절정|쾌감|황홀|신음|삽입|몸속|몸\s*안으로|안으로\s*밀|들어왔|밀어넣|"
    r"클라이막스|오르가슴|온몸이\s*떨|흘러내|흥분|"
    r"브래지어|팬티|속옷|맨살|벗겨|벗히|옷을\s*벗|벗어\s*던|알몸|나체|"
    r"가슴을\s*드러|허벅지\s*사이|살갗|노출된|살이\s*드러"
)
_OOC_SPAN_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)
_RULE_BASED_MAX_LEN = 60
_CLASSIFIER_TIMEOUT_SECONDS = 20


# ════════════════════════════════════════════════════════════
# 씬 분류 + 시간 계산 (통합)
# ════════════════════════════════════════════════════════════

_INTIMATE_RESULT = {
    "scene_types":     ["intimate"],
    "action_type":     "dialogue",
    "elapsed_minutes": 3,
    "new_weather":     None,
    "new_location_id": None,
    "reason":          "rule-based: intimate",
}


def _try_rule_based(user_input: str, recent_story: str = "") -> dict | None:
    # 1. OOC 포함 원문에서 intimate 키워드 감지 (팬티/자지 등이 *...*블록에 있을 수 있음)
    if _INTIMATE_INPUT_PATTERN.search(user_input):
        return _INTIMATE_RESULT

    # 2. recent_story 마지막 600자에 intimate 컨텍스트가 있으면
    if recent_story and _INTIMATE_CONTEXT_PATTERN.search(recent_story[-600:]):
        rule_input = _OOC_SPAN_RE.sub("", user_input).strip() or user_input
        if len(rule_input) <= _RULE_BASED_MAX_LEN and not _NEEDS_LLM_PATTERN.search(rule_input):
            return _INTIMATE_RESULT
        return None

    # 3. 일반 rule-based: OOC 제거 후 짧은 입력만 처리
    rule_input = _OOC_SPAN_RE.sub("", user_input).strip() or user_input
    if len(rule_input) > _RULE_BASED_MAX_LEN:
        return None
    if _NEEDS_LLM_PATTERN.search(rule_input):
        return None
    return {
        "scene_types":     ["daily"],
        "action_type":     "dialogue",
        "elapsed_minutes": 2,
        "new_weather":     None,
        "new_location_id": None,
        "reason":          "rule-based: short dialogue",
    }


async def _classify_and_parse_time(
    user_input:        str,
    recent_story:      str,
    global_state:      dict,
    allowed_locs:      str,
    scene_descriptions: dict[str, str] | None = None,
) -> dict:
    current_time    = datetime.fromisoformat(global_state["currentTime"])
    context_snippet = recent_story[-800:] if recent_story else ""
    _scenes = scene_descriptions or {"daily": "Everyday life with no significant conflict"}
    scene_types_block = "\n".join(f"  - {name}: {desc}" for name, desc in _scenes.items())

    system_instruction = "You are a combined scene classifier and time parser for a Korean roleplay system."

    prompt = f"""Analyze the user input and return a single JSON object. No explanation, no markdown.
Return ONLY valid JSON. No markdown fences. No ellipsis. No truncation.
If a field is uncertain, use null — never use "...".

[Current World State]
Time: {current_time.strftime("%Y-%m-%d %H:%M")} | Weather: {global_state["weather"]} | Location: {global_state["currentLocationId"]}

[Allowed Locations]
{allowed_locs}

[Context]
{context_snippet}

[User Input]
{user_input}

[Rules]
scene_types: pick 1+ from the list below (use exact keys):
{scene_types_block}
action_type: "dialogue"(3min) | "action"(10min) | "movement"(25min) | "ooc_jump"(null min, use target_hour)
target_hour: int (0-23) only for ooc_jump. Map: 새벽→3, 아침→8, 점심→12, 오후→15, 저녁→19, 밤→23
new_location_id:
  - If the destination exists in Allowed Locations, use that exact existing ID.
  - If the destination is clearly a new concrete place, create a stable lowercase snake_case ID.
  - Use null only when no location change is requested.
new_location:
  - null when using an existing location or no location change.
  - object only when new_location_id is a new ID not listed in Allowed Locations.
  - shape: {{"name": "display name", "description": "short factual description", "prompt_hint": "sensory/context hint", "parent_location_id": "existing_parent_id_or_null", "tags": ["dynamic"], "prompt_priority": 8}}
  - parent_location_id should be the nearest existing broader place from Allowed Locations, or null for a new region/trip.
  - Use concise concrete metadata. Do not invent detailed lore beyond the user input and immediate context.
new_weather: from [Clear,Cloudy,Foggy,Drizzle,Rain,Heavy Rain,Thunderstorm,Snow,Heavy Snow,Windy] or null

[Output — ONLY this JSON]
{{
  "scene_types": [...],
  "action_type": "...",
  "target_hour": null,
  "elapsed_minutes": 3,
  "new_weather": null,
  "new_location_id": null,
  "new_location": null,
  "reason": "..."
}}
"""

    try:
        model = get_model(model_name=CLASSIFIER_MODEL, system_prompt=system_instruction)

        resp = await asyncio.wait_for(
            model.generate_content_async(
                prompt,
                generation_config={
                    "max_output_tokens": 256,
                    "temperature": 0.0,
                    "thinking_config": {"thinking_level": "LOW"},
                    "response_mime_type": "application/json",
                },
            ),
            timeout=_CLASSIFIER_TIMEOUT_SECONDS,
        )
        raw    = get_response_text(resp)
        parsed = extract_json_from_llm(raw, source="manager_agent")
        if not isinstance(parsed, dict) or "scene_types" not in parsed:
            raise ValueError("invalid structure")
        print(f"[Classify+Time / {CLASSIFIER_MODEL}] scene={parsed.get('scene_types')} elapsed={parsed.get('elapsed_minutes')}min")
        return parsed
    except asyncio.TimeoutError:
        print(f"[Classify+Time timeout] {CLASSIFIER_MODEL} > {_CLASSIFIER_TIMEOUT_SECONDS}s -> fallback")
        return {
            "scene_types":     ["daily"],
            "action_type":     "dialogue",
            "elapsed_minutes": 3,
            "new_weather":     None,
            "new_location_id": None,
        }
    except Exception as e:
        print(f"[Classify+Time 실패] {e} → fallback")
        return {
            "scene_types":     ["daily"],
            "action_type":     "dialogue",
            "elapsed_minutes": 3,
            "new_weather":     None,
            "new_location_id": None,
        }


# ════════════════════════════════════════════════════════════
