# ================================
# src/agents/manager/effects.py
#
# Commit manager-planned core and auxiliary side effects.
#
# Functions
#   - commit_manager_effects(effects: dict | None, pc_id: str, npc_id: str, scene_chars: list[str] | None = None) -> dict : Commit pending manager side effects
#   - commit_manager_core_effects(effects: dict | None, pc_id: str, npc_id: str) -> dict : Commit core time effects
#   - commit_manager_auxiliary_effects(effects: dict | None, pc_id: str, npc_id: str, current_dt: datetime | None, scene_chars: list[str] | None = None) -> dict : Commit best-effort manager side effects
# ================================
from datetime import datetime

from src.simulation.events import evaluate_all as evaluate_static_events
from src.simulation.state.updater import commit_time_plan
from src.simulation.systems.memory import run_decay
from src.simulation.systems.needs import run_needs_update
from src.simulation.systems.organic import tick_all_cycles
from src.simulation.systems.personal_facts import commit_personal_facts
from src.simulation.systems.schedule_tick import run_schedule_tick
from src.simulation.systems.schedules import _fetch_schedule_rows


def _parse_effect_datetime(value: object) -> datetime | None:
    """Parse an ISO datetime from manager effect data."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _effect_base_time(effects: dict) -> datetime | None:
    """Return the previous in-game time for time-plan or OOC time patches."""
    time_plan = effects.get("time_plan") or {}
    ooc_patch = effects.get("ooc_time_patch") or {}
    return _parse_effect_datetime(time_plan.get("base_time") or ooc_patch.get("time_before"))


async def commit_manager_core_effects(
    effects: dict | None,
    pc_id: str,
    npc_id: str,
) -> dict:
    """Commit manager effects that must succeed before pending can be cleared."""
    if not effects:
        return {"current_dt": None}

    time_plan = effects.get("time_plan")
    current_dt: datetime | None = None
    if time_plan:
        current_dt = await commit_time_plan(time_plan, pc_id, npc_id)
    elif effects.get("ooc_time_after"):
        current_dt = _parse_effect_datetime(effects.get("ooc_time_after"))

    return {"current_dt": current_dt}


async def commit_manager_auxiliary_effects(
    effects: dict | None,
    pc_id: str,
    npc_id: str,
    current_dt: datetime | None,
    scene_chars: list[str] | None = None,
) -> dict:
    """Commit best-effort manager side effects after core commit succeeds."""
    if not effects:
        return {}

    needs_result: dict = {}
    schedule_rows: list[dict] | None = None

    if current_dt:
        try:
            schedule_rows = await _fetch_schedule_rows()
        except Exception as e:
            print(f"[ManagerCommit] schedule fetch failed (ignored): {e}")

    needs_plan = effects.get("needs_update") or {}
    if needs_plan:
        try:
            needs_time = current_dt or datetime.fromisoformat(needs_plan["current_time"])
            elapsed_minutes = needs_plan.get("elapsed_minutes")
            needs_result = await run_needs_update(
                pc_id           = needs_plan.get("pc_id") or pc_id,
                elapsed_minutes = float(elapsed_minutes if elapsed_minutes is not None else 1.0),
                current_time    = needs_time,
                scene_chars     = scene_chars,
                schedule_rows   = schedule_rows,
            )
        except Exception as e:
            print(f"[ManagerCommit] needs update failed (ignored): {e}")

    daily_plan = effects.get("daily_systems") or {}
    try:
        days_passed = int(daily_plan.get("days_passed") or 0)
    except (TypeError, ValueError):
        days_passed = 0
    daily_time = current_dt or _parse_effect_datetime(daily_plan.get("current_time"))
    if days_passed > 0 and daily_time:
        try:
            await run_decay(daily_time)
        except Exception as e:
            print(f"[ManagerCommit] decay failed (ignored): {e}")
        try:
            await tick_all_cycles(days_passed)
        except Exception as e:
            print(f"[ManagerCommit] cycle tick failed (ignored): {e}")

    personal_facts = effects.get("personal_facts") or []
    if personal_facts:
        try:
            await commit_personal_facts(personal_facts, pc_id, npc_id, current_dt)
        except Exception as e:
            print(f"[ManagerCommit] personal facts update failed (ignored): {e}")

    if current_dt:
        try:
            await evaluate_static_events(current_dt, commit=True)
        except Exception as e:
            print(f"[ManagerCommit] StaticEvent evaluation failed (ignored): {e}")

    if current_dt:
        try:
            prev_dt = _effect_base_time(effects)
            if prev_dt:
                await run_schedule_tick(
                    pc_id=pc_id,
                    npc_id=npc_id,
                    prev_time=prev_dt,
                    current_time=current_dt,
                    scene_chars=scene_chars,
                    schedule_rows=schedule_rows,
                )
        except Exception as e:
            print(f"[ManagerCommit] schedule tick failed (ignored): {e}")

    return needs_result


async def commit_manager_effects(
    effects: dict | None,
    pc_id: str,
    npc_id: str,
    scene_chars: list[str] | None = None,
) -> dict:
    """Actor 응답이 확정된 턴의 manager side effect를 DB에 반영합니다."""
    if not effects:
        return {}

    core_result = await commit_manager_core_effects(effects, pc_id, npc_id)
    return await commit_manager_auxiliary_effects(
        effects,
        pc_id=pc_id,
        npc_id=npc_id,
        current_dt=core_result.get("current_dt"),
        scene_chars=scene_chars,
    )


# ════════════════════════════════════════════════════════════
