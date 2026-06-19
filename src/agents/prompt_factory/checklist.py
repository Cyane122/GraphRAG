# ================================
# src/agents/prompt_factory/checklist.py
#
# Actor prompt checklist rendering helpers.
#
# Functions
#   - build_turn_checklist(template: str, scene_types: list[str], world_config: dict, char_data: dict, current_pov: dict | None, npc_data_list: list[dict] | None, char_name: str = "", user_name: str = "") -> str : Render per-turn checklist text
# ================================

from src.agents.prompt_factory.renderers import _SafeFormatDict, render_state_line



def build_turn_checklist(
    template: str,
    scene_types: list[str],
    world_config: dict,
    char_data: dict,
    current_pov: dict | None = None,
    npc_data_list: list[dict] | None = None,
    char_name: str = "",
    user_name: str = "",
) -> str:
    """Render checklist placeholders that depend on current scene and DynamicState."""
    scene_scan = _build_intimate_scan(scene_types, world_config, char_data, char_name, user_name)
    checklist = template.replace("{scene_specific_scan}", scene_scan)
    checklist = checklist.replace("{intimate_scan}", scene_scan)
    dyn_state = char_data.get("dynamic_state", {})
    checklist = checklist.replace("{cycle_line}", _build_all_cycle_lines(char_data, npc_data_list or []))
    checklist = checklist.replace("{state_line}", render_state_line(dyn_state, world_config))
    checklist = checklist.replace("{current_pov_line}", _render_current_pov_line(current_pov or {}))
    checklist = checklist.replace(
        "{world_cot_append}",
        _format_scene_vars(
            world_config.get("world_cot_append", "").strip(),
            world_config,
            char_data,
            char_name,
            user_name,
        ),
    )
    return checklist


def _build_intimate_scan(
    scene_types: list[str],
    world_config: dict,
    char_data: dict,
    char_name: str,
    user_name: str,
) -> str:
    """Return per-scene checklist append items for all active scene types.

    New path: prompt.scenes.checklist_append dict (keyed by scene type,
    populated from scenes/<type>.checklist_append.md files).
    Legacy path: intimate_checklist_items + intimate_checklist_scene_types flat keys.
    """
    checklist_append = world_config.get("prompt", {}).get("scenes", {}).get("checklist_append", {})
    if checklist_append:
        parts = [
            _format_scene_vars(v.strip(), world_config, char_data, char_name, user_name)
            for k in scene_types
            if (v := checklist_append.get(k))
        ]
        return "\n".join(parts)

    trigger_types = frozenset(world_config.get("intimate_checklist_scene_types") or ["intimate"])
    if not trigger_types.intersection(scene_types):
        return ""
    return _format_scene_vars(
        world_config.get(
            "intimate_checklist_items",
            "- Preparation: own body absent from prep narration\n"
            "- Penetration: entry collapsed into single verb without physical resistance beat",
        ),
        world_config,
        char_data,
        char_name,
        user_name,
    )


def _format_scene_vars(
    text: str,
    world_config: dict,
    char_data: dict,
    char_name: str,
    user_name: str,
) -> str:
    """Apply common scene placeholders while preserving unknown checklist placeholders."""
    if not text:
        return ""
    return text.format_map(
        _SafeFormatDict(
            char=str(char_name or char_data.get("name") or world_config.get("npc_name_kor") or ""),
            user=str(user_name or world_config.get("pc_name_kor") or world_config.get("user_name") or "사용자"),
        )
    )


# 임신 단계 묘사 가이드.
#   - DB의 pregnancy_day는 일(day) 단위이므로 의학적 "주(week)"를 일 범위로 변환했다
#     (기존 코드 관례와 동일: 일수 = 주수 × 7, 주 N = (N-1)*7+1 ~ N*7일).
#   - 정확한 일수/주수는 프롬프트에 노출하지 않는다(모델이 "53일째/N주차"로 받아쓰는 것을
#     막기 위함). 대신 현재 단계의 몸 상태 + 묘사 포인트만 정성적으로 흘린다.
#   - (max_day, 단계, 몸 상태, 묘사 포인트) — preg_day <= max_day인 첫 항목을 채택.
_PREGNANCY_STAGES: list[tuple[int, str, str, str]] = [
    (28,  "very early",
          "Often unaware herself. Missed period, faint fatigue. No visible change.",
          "Keep it subtle: \"body feels oddly heavy\", \"the smell of coffee is off-putting\"."),
    (56,  "early",
          "Pregnancy noticed/confirmed. Fatigue, drowsiness, breast tenderness, nausea, scent sensitivity. Almost no visible change.",
          "Show it as condition suddenly collapsing, not through appearance."),
    (84,  "late-early",
          "Nausea, mood swings, frequent urination may stand out. Belly still barely shows.",
          "\"More irritable than usual\", \"tears up out of nowhere\", \"goes to the bathroom often\"."),
    (112, "early-mid",
          "Nausea and fatigue ease. Belly starts to show just slightly.",
          "Ambiguous to others, but her own clothes start to feel tight."),
    (140, "mid",
          "Belly starts to look like a pregnant belly. Often feels the first fetal movement.",
          "First kicks: \"a light tap\", \"like a fish brushing past\", \"a bubble popping inside\"."),
    (168, "late-mid",
          "Belly noticeably out; lower-back/pelvis strain rises. Skin changes, stretch marks, breast changes possible.",
          "Hard to stand for long; conscious of the belly when sitting. Use kicks in emotional beats."),
    (196, "end-of-mid",
          "Sleep discomfort, back pain, belly tightening, indigestion may increase.",
          "\"Has to lie on her side\", \"rests a hand to cradle the belly\", \"walks slower\"."),
    (224, "early-late",
          "Shortness of breath, frequent urination, leg swelling, false contractions possible.",
          "Movement gets sluggish; stairs and long walks feel taxing."),
    (252, "late",
          "Pressure on stomach/lungs/bladder intensifies. Hard to sleep, tires easily.",
          "\"Bothersome to get up once seated\", \"pauses for breath mid-sentence\", \"belly tightens often\"."),
    (10_000, "near-term",
          "Belly feels dropped low. False labor, mucus plug, water breaking possible.",
          "Tension rising. Hospital bag, timing contractions, overprotective people around her."),
]


def _cycle_status(dyn_state: dict) -> str:
    """단일 캐릭터의 생리 주기 상태를 한 줄로 반환합니다 (플래그 문구 제외)."""
    cycle_day = int(dyn_state.get("cycle_day") or 1)
    pregnant  = bool(dyn_state.get("pregnant") or False)
    preg_day  = int(dyn_state.get("pregnancy_day") or 0)

    if pregnant:
        stage, body, hint = next(
            ((s, body, hint) for max_day, s, body, hint in _PREGNANCY_STAGES if preg_day <= max_day),
            _PREGNANCY_STAGES[-1][1:],
        )
        return (
            f"pregnant [{stage}] {body} CUES: {hint} "
            "NOTE: never count or state exact days/weeks - people do not recall dates precisely."
        )

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
    # cycle_day 정수는 노출하지 않는다(국면·가임 위험만으로 충분; "N일째" 받아쓰기 방지).
    return f"{phase} / pregnancy_risk={risk}"


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
    facts = _compact_pov_facts(selected)
    facts_part = f" | canon={facts}" if facts else ""
    return f"{label} | source={source} | access=current_pov only{facts_part}"


def _compact_pov_facts(selected: dict) -> str:
    """Return stable current-POV canon facts that prevent identity hallucination."""
    profile = selected.get("profile") or {}
    dynamic_state = selected.get("dynamic_state") or {}
    fields = [
        ("age", profile.get("age")),
        ("gender", profile.get("gender")),
        ("role", profile.get("role")),
        ("status", profile.get("current_status") or dynamic_state.get("current_status")),
        ("location", dynamic_state.get("location_id")),
    ]
    parts = [f"{key}={value}" for key, value in fields if value not in (None, "")]
    return "; ".join(parts)
