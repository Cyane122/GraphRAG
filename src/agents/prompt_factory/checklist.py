# ================================
# src/agents/prompt_factory/checklist.py
#
# Actor prompt checklist rendering helpers.
#
# Functions
#   - build_turn_checklist(template: str, scene_types: list[str], world_config: dict, char_data: dict, current_pov: dict | None, npc_data_list: list[dict] | None) -> str : Render per-turn checklist text
# ================================

from src.agents.prompt_factory.renderers import render_state_line


_CYCLE_PLACEHOLDER = (
    "CYCLE: day=[cycle_day from DynamicState, 1~28; 29~] "
    "phase=[생리(1~5)/난포기(6~9)/가임기(10~17)/황체기(18~28)] "
    "pregnancy_risk=[있음(10~17, 배란 피크=14일) / 없음] "
    "If condom omitted AND pregnancy_risk=있음 -> flag in interior monologue."
)

def build_turn_checklist(
    template: str,
    scene_types: list[str],
    world_config: dict,
    char_data: dict,
    current_pov: dict | None = None,
    npc_data_list: list[dict] | None = None,
) -> str:
    """Render checklist placeholders that depend on current scene and DynamicState."""
    checklist = template.replace("{intimate_scan}", _build_intimate_scan(scene_types, world_config))
    dyn_state = char_data.get("dynamic_state", {})
    checklist = checklist.replace(_CYCLE_PLACEHOLDER, _build_all_cycle_lines(char_data, npc_data_list or []))
    checklist = checklist.replace("{state_line}", render_state_line(dyn_state, world_config))
    checklist = checklist.replace("{current_pov_line}", _render_current_pov_line(current_pov or {}))
    checklist = checklist.replace("{world_cot_append}", world_config.get("world_cot_append", "").strip())
    return checklist


def _build_intimate_scan(scene_types: list[str], world_config: dict) -> str:
    """Return per-scene checklist append items for all active scene types.

    New path: prompt.scenes.checklist_append dict (keyed by scene type,
    populated from scenes/<type>.checklist_append.md files).
    Legacy path: intimate_checklist_items + intimate_checklist_scene_types flat keys.
    """
    checklist_append = world_config.get("prompt", {}).get("scenes", {}).get("checklist_append", {})
    if checklist_append:
        parts = [v.strip() for k in scene_types if (v := checklist_append.get(k))]
        return "\n".join(parts)

    trigger_types = frozenset(world_config.get("intimate_checklist_scene_types") or ["intimate"])
    if not trigger_types.intersection(scene_types):
        return ""
    return world_config.get(
        "intimate_checklist_items",
        "- Preparation: own body absent from prep narration\n"
        "- Penetration: entry collapsed into single verb without physical resistance beat",
    )


def _cycle_status(dyn_state: dict) -> str:
    """단일 캐릭터의 생리 주기 상태를 한 줄로 반환합니다 (플래그 문구 제외)."""
    cycle_day = int(dyn_state.get("cycle_day") or 1)
    pregnant  = bool(dyn_state.get("pregnant") or False)
    preg_day  = int(dyn_state.get("pregnancy_day") or 0)

    if pregnant:
        trimester = "late" if preg_day >= 91 else ("early" if preg_day < 42 else "mid")
        return f"pregnant day={preg_day} ({trimester})"

    phase_ranges = {
        range(1, 6):  ("생리 중", False),
        range(6, 10): ("난포기",  False),
        range(10, 18):("가임기",  True),
        range(18, 29):("황체기",  False),
    }
    phase, fertile = next(
        (v for r, v in phase_ranges.items() if cycle_day in r),
        ("황체기", False),
    )
    risk = "있음" + (" (배란 피크)" if cycle_day == 14 else "") if fertile else "없음"
    return f"day={cycle_day} / {phase} / pregnancy_risk={risk}"


def _build_cycle_line(dyn_state: dict) -> str:
    """단일 캐릭터 CYCLE 체크리스트 라인 (레거시 호환용)."""
    if dyn_state.get("has_menstrual_cycle") is False:
        return "CYCLE: n/a"
    return (
        f"CYCLE: {_cycle_status(dyn_state)} "
        "-> If condom omitted AND pregnancy_risk=있음 -> flag in interior monologue."
    )


def _build_all_cycle_lines(char_data: dict, npc_data_list: list[dict]) -> str:
    """등장 중인 모든 여성 캐릭터의 생리 주기를 CoT에 출력합니다."""
    entries: list[tuple[str, dict]] = []
    seen_names: set[str] = set()

    for src in [char_data, *npc_data_list]:
        dyn = src.get("dynamic_state", {})
        name = src.get("name", "?")
        if dyn.get("has_menstrual_cycle") is True and name not in seen_names:
            entries.append((name, dyn))
            seen_names.add(name)

    if not entries:
        return "CYCLE: n/a"

    if len(entries) == 1:
        name, dyn = entries[0]
        return f"CYCLE [{name}]: {_cycle_status(dyn)} -> If condom omitted AND pregnancy_risk=있음 -> flag."

    lines = ["CYCLE (등장 여성):"]
    has_risk = False
    for name, dyn in entries:
        status = _cycle_status(dyn)
        lines.append(f"  {name}: {status}")
        if "pregnancy_risk=있음" in status:
            has_risk = True
    if has_risk:
        lines.append("-> pregnancy_risk=있음 캐릭터 존재 — condom 생략 시 interior monologue에 명시.")
    return "\n".join(lines)


def _render_current_pov_line(current_pov: dict) -> str:
    """Render the selected POV candidate as one compact checklist phrase."""
    selected = current_pov.get("selected") or {}
    if not selected:
        return "unknown -> keep narrator as current primary character."
    name = str(selected.get("name") or selected.get("id") or "unknown").strip()
    char_id = str(selected.get("id") or "").strip()
    source = str(selected.get("source") or "unknown").strip()
    label = f"{name}({char_id})" if char_id and char_id != name else name
    return f"{label} | source={source} | access=current_pov only"
