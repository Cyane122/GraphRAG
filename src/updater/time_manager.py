# src/updater/time_manager.py
"""
시간 DB 사이드이펙트 전담 모듈.

LLM 호출 제거 — 씬 분류 + 시간 계산은 manager_agent._classify_and_parse_time()으로 이전됨.
이 파일은 계산된 plan을 받아 DB에만 반영.
"""

from datetime import datetime, timedelta

from src.utils.db_utils import async_driver, move_location, advance_cycle_day


async def apply_time_updates(
    plan:      dict,
    base_time: datetime,
    pc_id:     str,
    npc_id:    str,
) -> datetime:
    """
    manager_agent에서 계산된 plan을 받아 GlobalState + 캐릭터 DB 반영.

    Returns: 새로운 인게임 datetime (이후 파이프라인에서 사용)
    """
    action_type = plan.get("action_type", "dialogue")

    # ── 새로운 시각 계산 ────────────────────────────────────
    if action_type == "ooc_jump" and plan.get("target_hour") is not None:
        target_hour = int(plan["target_hour"])
        days_to_add = 1 if target_hour <= base_time.hour else 0
        new_time    = base_time.replace(hour=target_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_to_add)
    else:
        minutes = plan.get("elapsed_minutes")
        if not isinstance(minutes, int) or not (0 < minutes < 10080):  # 최대 1주일
            minutes = 3
        new_time = base_time + timedelta(minutes=minutes)

    days_passed = (new_time.date() - base_time.date()).days

    # ── GlobalState 업데이트 ────────────────────────────────
    update_fields = ["gs.currentTime = $new_time"]
    params: dict  = {"new_time": new_time.isoformat()}

    new_weather = plan.get("new_weather")
    if new_weather and new_weather != "null":
        update_fields.append("gs.weather = $weather")
        params["weather"] = new_weather

    new_loc_id = plan.get("new_location_id")
    if new_loc_id and new_loc_id != "null":
        update_fields.append("gs.currentLocationId = $loc_id")
        params["loc_id"] = new_loc_id

    try:
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET {', '.join(update_fields)}",
                **params,
            )

        # 날짜 변경 시 생리주기 진행
        if days_passed > 0:
            for char_id in (pc_id, npc_id):
                await advance_cycle_day(char_id, days_passed)

        # 장소 이동
        if new_loc_id and new_loc_id != "null":
            for char_id in (pc_id, npc_id):
                await move_location(char_id, new_loc_id)

        print(f"[TimeManager] {new_time.strftime('%Y-%m-%d %H:%M')} | {plan.get('reason', '')}")

    except Exception as e:
        print(f"[TimeManager] DB 업데이트 실패: {e}")

    return new_time