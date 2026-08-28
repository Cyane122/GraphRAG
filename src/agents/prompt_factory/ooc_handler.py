# ================================
# src/agents/prompt_factory/ooc_handler.py
#
# Detect and parse OOC (*...*) commands into a state-application-ready plan without persistent mutation.
#
# Functions
#   - is_ooc(text: str) -> bool : Return whether text contains an OOC marker
#   - parse_ooc(text: str, npc_id: str, npc_name: str, pc_id: str | None = None, world_config: dict | None = None) -> dict : Parse an OOC command without applying persistent state
# ================================
from __future__ import annotations

import re
from pathlib import Path

from src.config import MODEL_STATE_UPDATER as OOC_MODEL
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text
from src.simulation.state.apply.ooc import _DATE_KOR_RE, _NEXT_MORNING_RE, _THREE_HOURS_LATER_RE, _coerce_delta_minutes, build_ooc_parse_context

_BOLD_RE = re.compile(r'\*\*.*?\*\*', re.DOTALL)

def _compact_prompt_text(text: object, limit: int) -> str:
    """프롬프트 주입용 텍스트를 공백 정리 후 길이 제한합니다."""
    value = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"

_SYSTEM_PROMPT = (Path(__file__).resolve().parent / "prompts" / "ooc" / "system.md").read_text(encoding="utf-8")

async def _render_ooc_world_context(world_config: dict | None, rule_hints: list[str]) -> str:
    """월드/시나리오 프롬프트와 Rule 힌트를 OOC 파서용 컨텍스트로 렌더링합니다."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []

    world_lore = _compact_prompt_text(sections.get("world"), 1800)
    if world_lore:
        parts.append("### World Lore\n" + world_lore)

    scenario_lore = _compact_prompt_text(sections.get("scenario"), 5000)
    if scenario_lore:
        parts.append("### Scenario Lore\n" + scenario_lore)

    focus_map = (world_config or {}).get("prompt", {}).get("characters", {}).get("focus", {})
    focus_text = _compact_prompt_text("\n\n".join(str(v) for v in focus_map.values() if v), 1800)
    if focus_text:
        parts.append("### Character Focus\n" + focus_text)

    rule_hints = rule_hints
    if rule_hints:
        parts.append("### Active Rules\n" + "\n".join(rule_hints))

    return "\n\n".join(parts) if parts else "none"

def _render_schedule_context_for_ooc(schedule_context: dict) -> str:
    """스케줄 컨텍스트를 OOC 시간 파서용 텍스트로 렌더링합니다."""
    schedules = schedule_context.get("schedules") or []
    routines = schedule_context.get("routine_schedules") or []
    lines: list[str] = []

    if schedules:
        lines.append("Same-day schedules:")
        for s in schedules[:6]:
            owner = s.get("owner_name") or s.get("owner_id") or "character"
            name = s.get("name") or s.get("activity") or "schedule"
            start = s.get("start_time") or "?"
            end = s.get("end_time") or "?"
            location = s.get("location_name") or s.get("location_id") or "?"
            timing = s.get("timing") or "today"
            lines.append(f"- {owner}: {name} {start}-{end} at {location} ({timing})")

    today_routines = [r for r in routines if r.get("is_today")]
    if today_routines:
        lines.append("Today routines:")
        for s in today_routines[:6]:
            owner = s.get("owner_name") or s.get("owner_id") or "character"
            name = s.get("name") or s.get("activity") or "routine"
            start = s.get("start_time") or "?"
            end = s.get("end_time") or "?"
            lines.append(f"- {owner}: {name} {start}-{end}")

    return "\n".join(lines) if lines else "none"

def is_ooc(text: str) -> bool:
    """텍스트에 단일 별표 OOC 마커가 포함되어 있는지 반환합니다."""
    stripped = _BOLD_RE.sub('', text)
    return '*' in stripped

def _augment_time_plan_from_text(text: str, plan: dict, current_time: datetime) -> dict:
    """LLM이 흔한 시간 표현을 누락했을 때 deterministic rule로 보완합니다."""
    plan = dict(plan)

    if not plan.get("new_datetime"):
        m = _DATE_KOR_RE.search(text)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hour   = int(m.group(4)) if m.group(4) else current_time.hour
            minute = int(m.group(5)) if m.group(5) else (current_time.minute if m.group(4) else 0)
            try:
                plan["new_datetime"] = datetime(year, month, day, hour, minute, 0).isoformat()
            except ValueError:
                pass

    if not plan.get("new_datetime"):
        if not plan.get("time_delta_minutes") and _THREE_HOURS_LATER_RE.search(text):
            plan["time_delta_minutes"] = 180
        if not plan.get("time_set") and _NEXT_MORNING_RE.search(text):
            plan["time_delta_minutes"] = max(_coerce_delta_minutes(plan.get("time_delta_minutes")), 1440)
            plan["time_set"] = "08:00"

    return plan

async def parse_ooc(text: str, npc_id: str, npc_name: str, pc_id: str | None = None, world_config: dict | None = None) -> dict:
    """Parse an OOC command and retain the state context needed for later application."""
    context = await build_ooc_parse_context()
    current_time = context["current_time"]
    schedule_context = context["schedule_context"]
    locations = context["locations"]
    characters = context["characters"]
    location_context = context["location_context"]
    world_context_block = await _render_ooc_world_context(world_config, context["rule_hints"])
    char_lines = []
    for c in characters:
        line = f'- id="{c["id"]}" name="{c["name"]}"'
        if c["aliases"]:
            line += f' (aliases: {", ".join(c["aliases"])})'
        char_lines.append(line)
    characters_str = "\n".join(char_lines) if char_lines else "- 등록된 캐릭터 없음"
    chain = location_context.get("chain") or []
    current_location_chain = " → ".join(f'{c["name"]} ({c["id"]})' for c in chain) if chain else "알 수 없음"
    schedule_block = _render_schedule_context_for_ooc(schedule_context)
    system_prompt = (_SYSTEM_PROMPT.replace("{locations_str}", locations).replace("{characters_str}", characters_str).replace("{current_time}", current_time.isoformat()).replace("{current_location_chain}", current_location_chain).replace("{schedule_block}", schedule_block).replace("{world_context_block}", world_context_block))
    model = get_model(model_name=OOC_MODEL, system_prompt=system_prompt)
    response = await model.generate_content_async(text, generation_config={"max_output_tokens": 4096, "temperature": 0.0, "thinking_config": {"thinking_budget": 0}, "response_mime_type": "application/json", "log_source": "ooc_parser"})
    plan = extract_json_from_llm(get_response_text(response), source="ooc_parser")
    if not plan:
        plan = {"state_changes": {}, "summary": "parse failed"}
    plan = _augment_time_plan_from_text(text, plan, current_time)
    return {"plan": plan, "current_time": current_time, "characters": characters, "location_context": location_context}
