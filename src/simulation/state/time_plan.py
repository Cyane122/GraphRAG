# ================================
# src/simulation/state/time_plan.py
#
# Apply Manager-planned time, weather, and location updates to the DB.
#
# Functions
#   - build_time_plan(plan: dict, base_time: datetime) -> dict : Build a DB-write-ready time plan
#   - commit_time_plan(time_plan: dict, pc_id: str, npc_id: str) -> datetime : Commit a prepared time plan to the DB
#   - apply_time_updates(plan: dict, base_time: datetime, pc_id: str, npc_id: str) -> datetime : Build and commit a time plan
# ================================
from datetime import datetime, timedelta

from src.core.database import async_driver, ensure_location, move_location

# ════════════════════════════════════════════════════════════
# 시간 업데이트
# ════════════════════════════════════════════════════════════

def build_time_plan(plan: dict, base_time: datetime) -> dict:
    """시간/날씨/위치 변경 계획을 DB write 없이 계산합니다."""
    action_type = plan.get("action_type", "dialogue")

    if action_type == "ooc_jump" and plan.get("target_hour") is not None:
        target_hour = _coerce_hour(plan.get("target_hour"))
        if target_hour is None:
            target_hour = base_time.hour
        days_to_add = 1 if target_hour <= base_time.hour else 0
        new_time    = base_time.replace(hour=target_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_to_add)
    else:
        minutes = _coerce_elapsed_minutes(plan.get("elapsed_minutes"))
        new_time = base_time + timedelta(minutes=minutes)

    elapsed_minutes = max(1.0, (new_time - base_time).total_seconds() / 60)
    new_weather = plan.get("new_weather")
    new_loc_id = plan.get("new_location_id")
    new_location = plan.get("new_location") if isinstance(plan.get("new_location"), dict) else None

    return {
        "action_type":      action_type,
        "base_time":        base_time.isoformat(),
        "new_time":         new_time.isoformat(),
        "elapsed_minutes":  elapsed_minutes,
        "days_passed":      (new_time.date() - base_time.date()).days,
        "new_weather":      new_weather if new_weather and new_weather != "null" else None,
        "new_location_id":  new_loc_id if new_loc_id and new_loc_id != "null" else None,
        "new_location":     new_location,
        "reason":           plan.get("reason", ""),
    }


def _coerce_elapsed_minutes(value: object, default: int = 3) -> int:
    """LLM 숫자 출력(int/float/숫자 문자열)을 분 단위 정수로 정규화한다."""
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default
    return minutes if 0 < minutes < 10080 else default


def _coerce_hour(value: object) -> int | None:
    """LLM target_hour 출력을 0..23 범위의 시각으로 정규화한다."""
    try:
        hour = int(float(value))
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


async def _location_exists(location_id: str) -> bool:
    """Location 노드 존재 여부를 확인한다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (l:Location {id: $loc_id}) RETURN l.id AS id",
            loc_id=location_id,
        )
        return await rec.single() is not None


async def _ensure_planned_location(time_plan: dict, new_loc_id: str | None) -> str | None:
    """Create a planned new Location when the classifier supplied metadata for it."""
    if not new_loc_id:
        return None
    if await _location_exists(new_loc_id):
        return new_loc_id

    new_location = time_plan.get("new_location")
    if not isinstance(new_location, dict):
        new_location = {}
    if not new_location.get("name"):
        new_location = {
            **new_location,
            "name": new_loc_id,
            "description": str(new_location.get("description") or "Dynamically introduced location."),
            "prompt_hint": str(new_location.get("prompt_hint") or "Current scene location."),
            "tags": list(new_location.get("tags") or ["dynamic"]),
            "prompt_priority": new_location.get("prompt_priority") or 6,
        }

    return await ensure_location(
        location_id=new_loc_id,
        name=str(new_location.get("name") or new_loc_id),
        description=str(new_location.get("description") or ""),
        prompt_hint=str(new_location.get("prompt_hint") or new_location.get("description") or ""),
        parent_location_id=new_location.get("parent_location_id"),
        tags=list(new_location.get("tags") or ["dynamic"]),
        prompt_priority=new_location.get("prompt_priority") or 8,
    )


def _esc(value: str) -> str:
    """KuzuDB Cypher 문자열 리터럴용 단순 이스케이프 (내부 신뢰 값 전용)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def commit_time_plan(time_plan: dict, pc_id: str, npc_id: str) -> datetime:
    """계산된 시간 계획을 GlobalState와 위치 관계에 확정 반영합니다."""
    new_time = datetime.fromisoformat(time_plan["new_time"])

    # KuzuDB parsed_parameter_expression.h:copy() 버그 우회:
    # SET 절에 $param 을 사용하면 쿼리 플래너가 KU_UNREACHABLE을 트리거하므로
    # 신뢰된 내부 값은 리터럴로 직접 삽입한다.
    set_clauses = [f"gs.currentTime = '{_esc(new_time.isoformat())}'"]

    if time_plan.get("new_weather"):
        set_clauses.append(f"gs.weather = '{_esc(time_plan['new_weather'])}'")

    new_loc_id = await _ensure_planned_location(time_plan, time_plan.get("new_location_id"))
    if new_loc_id:
        set_clauses.append(f"gs.currentLocationId = '{_esc(new_loc_id)}'")

    try:
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET {', '.join(set_clauses)}"
            )

        if new_loc_id:
            for char_id in (pc_id, npc_id):
                await move_location(char_id, new_loc_id)

        print(f"[TimeManager] {new_time.strftime('%Y-%m-%d %H:%M')} | {time_plan.get('reason', '')}")

    except Exception as e:
        print(f"[TimeManager] DB 업데이트 실패: {e}")
        raise

    return new_time


async def apply_time_updates(
    plan:      dict,
    base_time: datetime,
    pc_id:     str,
    npc_id:    str,
) -> datetime:
    """
    manager_agent에서 계산된 plan을 받아 GlobalState + 캐릭터 DB 반영.

    Returns: 새로운 인게임 datetime
    """
    time_plan = build_time_plan(plan, base_time)
    return await commit_time_plan(time_plan, pc_id, npc_id)
