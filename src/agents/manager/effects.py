# ================================
# src/agents/manager/effects.py
#
# Commit manager-planned time, needs, and daily-system side effects.
#
# Functions
#   - commit_manager_effects(effects: dict | None, pc_id: str, npc_id: str, scene_chars: list[str] | None = None) -> dict : Commit pending manager side effects
# ================================
from datetime import datetime

from src.simulation.events import evaluate_all as evaluate_static_events
from src.simulation.state.updater import commit_time_plan
from src.simulation.systems.memory import run_decay
from src.simulation.systems.needs import run_needs_update
from src.simulation.systems.organic import tick_all_cycles
from src.simulation.systems.personal_facts import commit_personal_facts


async def commit_manager_effects(
    effects: dict | None,
    pc_id: str,
    npc_id: str,
    scene_chars: list[str] | None = None,
) -> dict:
    """Actor 응답이 확정된 턴의 manager side effect를 DB에 반영합니다."""
    if not effects:
        return {}

    needs_result: dict = {}

    time_plan = effects.get("time_plan")
    current_dt: datetime | None = None
    if time_plan:
        current_dt = await commit_time_plan(time_plan, pc_id, npc_id)
    elif effects.get("ooc_time_after"):
        try:
            current_dt = datetime.fromisoformat(effects["ooc_time_after"])
        except (TypeError, ValueError):
            current_dt = None

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
            )
        except Exception as e:
            print(f"[ManagerCommit] needs update 실패 (무시): {e}")

    daily_plan = effects.get("daily_systems") or {}
    days_passed = int(daily_plan.get("days_passed") or 0)
    if days_passed > 0:
        daily_time = current_dt or datetime.fromisoformat(daily_plan["current_time"])
        try:
            await run_decay(daily_time)
        except Exception as e:
            print(f"[ManagerCommit] decay 실패 (무시): {e}")
        try:
            await tick_all_cycles(days_passed)
        except Exception as e:
            print(f"[ManagerCommit] cycle tick 실패 (무시): {e}")

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
            print(f"[ManagerCommit] StaticEvent 평가 실패 (무시): {e}")

    return needs_result


# ════════════════════════════════════════════════════════════
