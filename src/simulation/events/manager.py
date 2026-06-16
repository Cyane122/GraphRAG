# ================================
# src/simulation/events/manager.py
#
# StaticEvent 생명주기 관리.
# 매 턴 조건을 평가해 dormant → foreshadowing → active 상태를 갱신하고
# 활성 이벤트의 hint 목록을 반환합니다.
#
# Functions
#   - evaluate_all(current_dt: datetime, commit: bool = True) -> list[dict] : 모든 이벤트를 평가하고 활성 hint 목록을 반환합니다.
#   - set_flag(key: str, value: bool) -> None : GlobalState.flags에 플래그를 세팅합니다.
# ================================

import json
from datetime import datetime

from src.core.database import async_driver
from src.core.database.helpers import set_global_flag
from src.simulation.events.evaluator import evaluate_conditions


async def evaluate_all(current_dt: datetime, commit: bool = True) -> list[dict]:
    """
    dormant/foreshadowing 상태인 모든 StaticEvent를 평가해 상태를 갱신합니다.
    foreshadowing 또는 active 상태이고 hint가 있는 이벤트 목록을 반환합니다.

    반환 형식: [{"name": str, "hint": str, "status": "foreshadowing"|"active"}, ...]
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (e:StaticEvent)
            WHERE e.status IN ['dormant', 'foreshadowing']
            RETURN e.id                    AS id,
                   e.name                  AS name,
                   e.foreshadow_conditions AS fc,
                   e.foreshadow_hint       AS foreshadow_hint,
                   e.trigger_conditions    AS tc,
                   e.status               AS status
        """)
        rows = await rec.data()

    hints: list[dict] = []

    for row in rows:
        event_id = row["id"]
        name     = row["name"]
        hint     = row.get("foreshadow_hint") or ""
        status   = row["status"]

        try:
            fc = json.loads(row.get("fc") or "[]")
            tc = json.loads(row.get("tc") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue

        trigger_met    = await evaluate_conditions(tc, current_dt)
        foreshadow_met = await evaluate_conditions(fc, current_dt)

        if trigger_met:
            new_status = "active"
        elif foreshadow_met:
            new_status = "foreshadowing"
        else:
            new_status = status

        # Actor 응답 확정 전에는 prompt hint만 계산하고 상태 변경은 commit 단계로 미룬다.
        if commit and new_status != status:
            async with async_driver.session() as session:
                await session.run(
                    "MATCH (e:StaticEvent {id: $id}) SET e.status = $status",
                    id=event_id,
                    status=new_status,
                )
            print(f"[StaticEvent] '{name}' {status} → {new_status}")

        if new_status in ("foreshadowing", "active") and hint:
            hints.append({"name": name, "hint": hint, "status": new_status})

    return hints


async def set_flag(key: str, value: bool) -> None:
    """
    GlobalState.flags JSON에 플래그를 세팅합니다.
    Complex Updater에서 서사적 조건이 충족됐을 때 호출합니다.
    read-modify-write를 단일 트랜잭션으로 처리해 narrative 로그 등 다른 flags 갱신과
    충돌(lost-update)하지 않는다.
    """
    await set_global_flag(key, value)
    print(f"[StaticEvent] flag '{key}' = {value}")
