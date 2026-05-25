# ================================
# src/agents/manager/classifier.py
#
# Scene classification and time parsing for the Manager pipeline.
#
# Functions
#   - _try_rule_based(user_input: str, recent_story: str = "") -> dict | None : Fast-path classification for short inputs
#   - _classify_and_parse_time(user_input: str, recent_story: str, global_state: dict, allowed_locs: str, scene_descriptions: dict[str, str] | None = None, schedule_context: dict | None = None) -> dict : LLM-based scene and time parsing
#   - _generate_classifier_text(model: Any, prompt: str) -> str : Generate classifier JSON text with larger-budget retry
#   - _coerce_classifier_result(parsed: object) -> dict | None : Accept common JSON shape variants
#   - _log_invalid_classifier_result(raw: str, parsed: object) -> None : Print compact invalid classifier diagnostics
#   - _normalize_classifier_result(parsed: dict, scene_descriptions: dict[str, str]) -> dict : Repair empty classifier fields
#   - _fallback_classification() -> dict : Return a conservative daily scene parse
#   - _render_schedule_context_for_classifier(schedule_context: dict) -> str : Render schedule constraints for the classifier prompt
#   - _format_schedule_for_classifier(schedule: dict, detailed: bool) -> str : Format one schedule constraint line
#   - _format_time_rule_for_classifier(rule: dict) -> str : Format one time rule constraint line
# ================================
import asyncio
import re
from datetime import datetime
from typing import Any

from src.config import MODEL_CLASSIFIER as CLASSIFIER_MODEL
from src.core.llm.client import (
    extract_json_from_llm,
    get_model,
    get_response_text,
    log_empty_response_diagnostics,
)

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
    r"절정|쾌감|황홀|신음|삽입|몸속|몸\s*안으로|안으로\s*밀|안으로\s*들어왔|밀어넣|"
    r"클라이막스|오르가슴|온몸이\s*떨|흘러내|흥분|"
    r"브래지어|팬티|속옷|맨살|벗겨|벗히|옷을\s*벗|벗어\s*던|알몸|나체|"
    r"가슴을\s*드러|허벅지\s*사이|살갗|노출된|살이\s*드러"
)
_OOC_SPAN_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", re.DOTALL)
_RULE_BASED_MAX_LEN = 60
_CLASSIFIER_TIMEOUT_SECONDS = 20
_CLASSIFIER_OUTPUT_TOKENS = 1024
_CLASSIFIER_RETRY_OUTPUT_TOKENS = 2048


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


def _render_schedule_context_for_classifier(schedule_context: dict) -> str:
    """Render compact schedule constraints for the Manager time parser."""
    schedules = schedule_context.get("schedules") or []
    routines = schedule_context.get("routine_schedules") or []
    time_rules = schedule_context.get("time_rules") or []
    lines: list[str] = []

    if time_rules:
        lines.append("Time rules:")
        for rule in time_rules[:6]:
            lines.append(f"- {_format_time_rule_for_classifier(rule)}")

    if schedules:
        lines.append("Same-day schedules:")
        for schedule in schedules[:6]:
            lines.append(f"- {_format_schedule_for_classifier(schedule, detailed=True)}")

    today_routines = [schedule for schedule in routines if schedule.get("is_today")]
    if today_routines:
        lines.append("Today routines:")
        for schedule in today_routines[:6]:
            lines.append(f"- {_format_schedule_for_classifier(schedule, detailed=False)}")

    return "\n".join(lines) if lines else "none"


def _format_time_rule_for_classifier(rule: dict) -> str:
    """Format one time rule in a short, LLM-readable line."""
    name = rule.get("name") or rule.get("id") or "time rule"
    hint = rule.get("prompt_hint") or rule.get("summary") or ""
    location = rule.get("location_id") or "global"
    tags = ",".join(str(tag) for tag in rule.get("tags") or [])
    fields = [f"{name}", f"scope={location}"]
    if hint:
        fields.append(str(hint))
    if tags:
        fields.append(f"tags={tags}")
    return "; ".join(fields)


def _format_schedule_for_classifier(schedule: dict, detailed: bool) -> str:
    """Format one schedule in a short, LLM-readable line."""
    owner = schedule.get("owner_name") or schedule.get("owner_id") or "character"
    name = schedule.get("name") or schedule.get("activity") or "schedule"
    start = schedule.get("start_time") or "?"
    end = schedule.get("end_time") or "?"
    location = schedule.get("location_name") or schedule.get("location_id") or "unspecified location"
    timing = schedule.get("timing") or ("today" if schedule.get("is_today") else "routine")
    fields = [f"{owner}: {name}", f"{start}-{end}", f"at {location}", f"timing={timing}"]

    if detailed and schedule.get("minutes_until") is not None:
        fields.append(f"minutes_until={schedule.get('minutes_until')}")
    for key in ("preparation_time_min", "travel_time_min", "flexibility", "lateness_tolerance", "can_skip", "requires_transition_scene"):
        value = schedule.get(key)
        if value not in (None, "", []):
            fields.append(f"{key}={value}")

    return "; ".join(str(field) for field in fields)


def _try_rule_based(user_input: str, recent_story: str = "") -> dict | None:
    """Return a deterministic parse for short/simple inputs, or None when LLM parsing is needed."""
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
    schedule_context: dict | None = None,
) -> dict:
    current_time    = datetime.fromisoformat(global_state["currentTime"])
    context_snippet = recent_story[-800:] if recent_story else ""
    _scenes = scene_descriptions or {"daily": "Everyday life with no significant conflict"}
    scene_types_block = "\n".join(f"  - {name}: {desc}" for name, desc in _scenes.items())
    schedule_block = _render_schedule_context_for_classifier(schedule_context or {})

    system_instruction = "Korean roleplay scene classifier & time parser. Return ONLY valid JSON."

    prompt = f"""Return ONLY valid JSON. null=uncertain.

[State] {current_time.strftime("%Y-%m-%d %H:%M")} | {global_state["weather"]} | loc={global_state["currentLocationId"]}
[Locations] {allowed_locs}
[Schedule] {schedule_block}
[Context] {context_snippet}
[Input] {user_input}

[Fields]
scene_types: 1+ exact keys ↓
{scene_types_block}
action_type: dialogue/action/movement/ooc_jump
target_hour: 0-23 (ooc_jump only; 새벽=3,아침=8,점심=12,오후=15,저녁=19,밤=23)
elapsed_minutes: whole-scene clock time (not sum of parts)
  dialogue: 1-2min brief | 3-5min extended
  action: 1-3min single | 5-15min sustained
  movement: 3-8min same-floor | 15-30min cross-area
  ≤2-sentence input → 1-3min
movement sensitivity: treat follow/lead/accompany/enter/leave/arrive/go-to as movement when a destination is present. Korean examples: "A를 따라 복도로 이동", "A가 B를 데리고 방으로 감", "A와 B가 교실에 들어감" => movement + destination location.
schedule: active/upcoming=pressure on elapsed & movement. time_rules=stable constraints (school hrs/curfew/meals). No auto-teleport to schedule loc. Add prep/travel_time when collision. Routines=ref only; same-day active/upcoming=binding.
new_location_id: existing→exact ID / new concrete→snake_case / no destination or only within-same-spot motion→null. For follow/lead/accompany movement, use the destination for the whole scene.
new_location: null(existing/no change) | object(new ID only): {{"name","description","prompt_hint","parent_location_id","tags":["dynamic"],"prompt_priority":8}}. parent=nearest existing broader loc. Concise only.
new_weather: Clear/Cloudy/Foggy/Drizzle/Rain/Heavy Rain/Thunderstorm/Snow/Heavy Snow/Windy / null

[Output — ONLY this JSON]
{{
  "scene_types": [...],
  "action_type": "...",
  "target_hour": null,
  "elapsed_minutes": 2,
  "new_weather": null,
  "new_location_id": null,
  "new_location": null,
  "reason": "..."
}}
"""

    try:
        model = get_model(model_name=CLASSIFIER_MODEL, system_prompt=system_instruction)

        raw = await _generate_classifier_text(model, prompt)
        parsed = extract_json_from_llm(raw, source="manager_agent", log_errors=False)
        coerced = _coerce_classifier_result(parsed)
        if coerced is None:
            _log_invalid_classifier_result(raw, parsed)
            raise ValueError("invalid structure")
        parsed = coerced
        parsed = _normalize_classifier_result(parsed, _scenes)
        print(f"[Classify+Time / {CLASSIFIER_MODEL}] scene={parsed.get('scene_types')} elapsed={parsed.get('elapsed_minutes')}min")
        return parsed
    except asyncio.TimeoutError:
        print(f"[Classify+Time timeout] {CLASSIFIER_MODEL} > {_CLASSIFIER_TIMEOUT_SECONDS}s -> fallback")
        return _fallback_classification()
    except Exception as e:
        print(f"[Classify+Time 실패] {e} → fallback")
        return _fallback_classification()


async def _generate_classifier_text(model: Any, prompt: str) -> str:
    """Generate classifier JSON and retry with a larger budget if Gemini returns no text."""
    base_config = {
        "max_output_tokens": _CLASSIFIER_OUTPUT_TOKENS,
        "temperature": 0.0,
        "thinking_config": {"thinking_budget": 0},
        "log_source": "manager_classifier",
    }
    resp = await asyncio.wait_for(
        model.generate_content_async(
            prompt,
            generation_config={**base_config, "response_mime_type": "application/json"},
        ),
        timeout=_CLASSIFIER_TIMEOUT_SECONDS,
    )
    raw = get_response_text(resp)
    if raw.strip():
        return raw

    log_empty_response_diagnostics(resp, "manager_classifier:json_mode")
    print("[Classify+Time] empty JSON response -> retrying with larger JSON budget")
    retry_resp = await asyncio.wait_for(
        model.generate_content_async(
            prompt,
            generation_config={
                **base_config,
                "max_output_tokens": _CLASSIFIER_RETRY_OUTPUT_TOKENS,
                "response_mime_type": "application/json",
            },
        ),
        timeout=_CLASSIFIER_TIMEOUT_SECONDS,
    )
    retry_raw = get_response_text(retry_resp)
    if not retry_raw.strip():
        log_empty_response_diagnostics(retry_resp, "manager_classifier:retry")
    return retry_raw


def _coerce_classifier_result(parsed: object) -> dict | None:
    """Accept common classifier JSON variants and return a normalized dict shell."""
    if isinstance(parsed, list):
        dict_items = [item for item in parsed if isinstance(item, dict)]
        if len(dict_items) == 1:
            parsed = dict_items[0]
        else:
            return None

    if not isinstance(parsed, dict):
        return None

    result = dict(parsed)
    if "scene_types" not in result:
        for alias in ("scene_type", "scene", "scenes"):
            if alias in result:
                result["scene_types"] = result.get(alias)
                break
    return result if "scene_types" in result else None


def _log_invalid_classifier_result(raw: str, parsed: object) -> None:
    """Print compact diagnostics when classifier JSON has an unusable structure."""
    preview = (raw or "").replace("\n", "\\n")[:500]
    if len(raw or "") > 500:
        preview += "... [log truncated]"
    print(
        "[Classify+Time invalid structure] "
        f"parsed_type={type(parsed).__name__} raw={preview or '(empty)'}"
    )


def _normalize_classifier_result(parsed: dict, scene_descriptions: dict[str, str]) -> dict:
    """Repair structurally valid classifier JSON that contains empty or unusable fields."""
    repaired = dict(parsed)

    allowed_scene_list = list(scene_descriptions) or ["daily"]
    allowed_scenes = set(allowed_scene_list)
    raw_scenes = repaired.get("scene_types")
    if isinstance(raw_scenes, str):
        raw_scenes = [raw_scenes]
    if not isinstance(raw_scenes, list):
        raw_scenes = []

    scenes = [
        str(scene).strip()
        for scene in raw_scenes
        if str(scene or "").strip() in allowed_scenes
    ]
    if not scenes:
        scenes = ["daily" if "daily" in allowed_scenes else allowed_scene_list[0]]
    repaired["scene_types"] = scenes

    action_type = str(repaired.get("action_type") or "dialogue").strip().lower()
    if action_type not in {"dialogue", "action", "movement", "ooc_jump"}:
        action_type = "dialogue"
    repaired["action_type"] = action_type

    if repaired.get("elapsed_minutes") is None and action_type != "ooc_jump":
        repaired["elapsed_minutes"] = 2 if action_type == "dialogue" else 5

    for nullable_key in ("target_hour", "new_weather", "new_location_id", "new_location"):
        if repaired.get(nullable_key) in ("", "null", "None"):
            repaired[nullable_key] = None

    if not repaired.get("reason"):
        repaired["reason"] = "classifier output normalized"

    return repaired


def _fallback_classification() -> dict:
    """Return the conservative fallback used when manager scene/time parsing fails."""
    return {
        "scene_types":     ["daily"],
        "action_type":     "dialogue",
        "elapsed_minutes": 2,
        "new_weather":     None,
        "new_location_id": None,
    }


# ════════════════════════════════════════════════════════════
