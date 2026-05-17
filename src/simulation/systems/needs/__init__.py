# ================================
# src/simulation/systems/needs/__init__.py
#
# Public API for updating all NPC need states.
#
# Functions
#   - ensure_traits(char_id: str) -> dict : Generate and save missing trait_* fields from StaticProfile
#   - ensure_traits_for_characters(characters: list[dict]) -> dict : Initialize traits from a character list
#   - run_needs_update(pc_id: str, elapsed_minutes: float, current_time: datetime, scene_chars: list[str] | None) -> dict : Update all NPC need states
#   - _build_libido_resolve_context(profile: dict) -> str : libido 자율 해소 시 LLM에 넘길 추가 컨텍스트 문자열 생성
# ================================
import asyncio
from datetime import datetime, timedelta

from src.agents.resolver import NEED_DEFAULTS, SETTLE_LEVELS, resolve_action
from src.simulation.systems.needs.math import (
    AUTONOMOUS_NEEDS,
    NEED_BASE_RATES,
    THRESHOLD,
    _as_float,
    _build_libido_hint,
    _build_need_hint,
    _calc_multiplier,
    _count_overflows,
)
from src.simulation.systems.needs.store import (
    _apply_safety_decay,
    _fetch_all_npcs,
    _fetch_needs,
    _fetch_profile_props,
    _write_needs,
)
from src.simulation.systems.needs.traits import ensure_traits, ensure_traits_for_characters

async def run_needs_update(
    pc_id:           str,
    elapsed_minutes: float,
    current_time:    datetime,
    scene_chars:     list[str] | None = None,
) -> dict:
    """
    app.py에서 time_manager 직후 호출.

    Returns:
        {
            "libido_hints":      {npc_id: hint_str},
            "scene_need_hints":  {npc_id: hint_str},
            "events_created":    [event_id, ...]
        }
    """
    if elapsed_minutes <= 0:
        return {"libido_hints": {}, "scene_need_hints": {}, "events_created": []}

    _scene_set = set(scene_chars or [])
    npcs = await _fetch_all_npcs(exclude_id=pc_id)

    def _in_scene(npc: dict) -> bool:
        """scene_chars(한국어 이름/alias)와 npc의 name·aliases를 대조해 씬 소속 여부 반환."""
        if not _scene_set:
            return False
        tokens = {npc["id"], npc.get("name") or ""} | set(npc.get("aliases") or [])
        return bool(tokens & _scene_set)

    libido_hints:     dict[str, str] = {}
    scene_need_hints: dict[str, str] = {}
    events_created:   list[str]      = []

    trait_init = await ensure_traits_for_characters(npcs)
    initialized_traits = trait_init.get("initialized", {})

    for npc in npcs:
        npc_id = npc["id"]

        needs, profile = await asyncio.gather(
            _fetch_needs(npc_id),
            _fetch_profile_props(npc_id),
        )
        traits = initialized_traits.get(npc_id) or await ensure_traits(npc_id)

        if profile.get("libido_excluded", False):
            continue

        updates: dict[str, float] = {}

        for need_name, base_rate in NEED_BASE_RATES.items():
            old_val = _as_float(needs.get(need_name), NEED_DEFAULTS[need_name])

            if need_name == "safety":
                new_val = await _apply_safety_decay(npc_id, old_val, elapsed_minutes, current_time)
                updates[need_name] = new_val
                continue

            multiplier = _calc_multiplier(need_name, traits, needs, profile)
            eff_rate   = base_rate * multiplier
            overflow_cnt, settled_val = _count_overflows(
                old_val, elapsed_minutes, eff_rate,
                SETTLE_LEVELS.get(need_name, 0.2),
            )

            if need_name == "libido":
                if overflow_cnt == 0:
                    updates[need_name] = round(min(1.0, old_val + eff_rate * elapsed_minutes), 4)
                    continue

                # 씬 참여 중이면 Actor 프롬프트 hint로 전환하고 욕구를 소폭 감소
                if _in_scene(npc):
                    hint = _build_libido_hint(npc_id, profile, needs, traits)
                    if hint:
                        libido_hints[npc_id] = hint
                    updates[need_name] = round(SETTLE_LEVELS.get("libido", 0.10), 4)
                    continue

                # 씬 밖 + overflow: 자율 해소 이벤트 생성 (파트너/자위/성향별)
                minutes_until_overflow = max(0.0, (THRESHOLD - old_val) / eff_rate)
                overflow_time = current_time - timedelta(
                    minutes=max(0.0, elapsed_minutes - minutes_until_overflow)
                )
                extra_ctx = _build_libido_resolve_context(profile)
                result = await resolve_action(
                    npc_id, "libido", overflow_time,
                    needs.get("location_id", "unknown"),
                    profile.get("personality", ""),
                    traits,
                    extra_context=extra_ctx,
                )
                if result:
                    events_created.append(result["event_id"])
                settle = SETTLE_LEVELS.get("libido", 0.10)
                time_after = elapsed_minutes - minutes_until_overflow - _as_float(
                    result.get("duration_minutes") if result else None, 0.0
                )
                updates[need_name] = round(min(1.0, settle + eff_rate * max(0, time_after)), 4)
                continue

            if overflow_cnt == 0:
                updates[need_name] = round(min(1.0, old_val + eff_rate * elapsed_minutes), 4)

            elif overflow_cnt == 1 and need_name in AUTONOMOUS_NEEDS:
                minutes_until_overflow = max(0.0, (THRESHOLD - old_val) / eff_rate)
                overflow_time = current_time - timedelta(
                    minutes=max(0.0, elapsed_minutes - minutes_until_overflow)
                )
                # 현재 씬에 있는 캐릭터는 자율 이벤트 대신 Actor 프롬프트 힌트로 전환
                if _in_scene(npc):
                    hint = _build_need_hint(npc_id, need_name)
                    if hint:
                        scene_need_hints[npc_id] = hint
                    settle  = SETTLE_LEVELS.get(need_name, 0.2)
                    updates[need_name] = round(settle, 4)
                    continue
                personality = profile.get("personality", "")
                result = await resolve_action(
                    npc_id, need_name, overflow_time,
                    needs.get("location_id", "unknown"),
                    personality, traits,
                )
                if result:
                    events_created.append(result["event_id"])
                time_after_resolve = (
                    elapsed_minutes
                    - minutes_until_overflow
                    - _as_float(result.get("duration_minutes") if result else None, 0.0)
                )
                settle  = SETTLE_LEVELS.get(need_name, 0.2)
                new_val = min(1.0, settle + eff_rate * max(0, time_after_resolve))
                updates[need_name] = round(new_val, 4)

            else:
                updates[need_name] = round(settled_val, 4)

        await _write_needs(npc_id, updates)

    return {
        "libido_hints":     libido_hints,
        "scene_need_hints": scene_need_hints,
        "events_created":   events_created,
    }


def _build_libido_resolve_context(profile: dict) -> str:
    """
    libido 자율 해소 이벤트 생성 시 _decide_action()에 전달할 추가 컨텍스트를 만든다.
    파트너 선호도와 sexual_tendency를 바탕으로 LLM이 행동 유형을 결정하도록 안내한다.
    """
    parts: list[str] = []
    partner = profile.get("libido_partner") or ""
    if partner:
        parts.append(f"Preferred partner: {partner}. Attempt to resolve with them; fall back to solo if unavailable.")
    else:
        parts.append("No established partner — solo resolution (masturbation, cold shower, distraction).")
    tendency = profile.get("sexual_tendency") or []
    if isinstance(tendency, list) and tendency:
        parts.append(f"Sexual tendencies: {', '.join(tendency)}. Let this shape the specific behavior.")
    return " ".join(parts)


# ════════════════════════════════════════════════════════════
# 욕구 수치 계산
# ════════════════════════════════════════════════════════════
