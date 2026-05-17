# ================================
# src/ui/time_state.py
#
# Chainlit UI에서 사용하는 인게임 시간 조회, 복원, 테마 발행을 처리합니다.
#
# Functions
#   - hour_from_time_string(time_str: str | None) -> int | None : 시간 문자열에서 시각 추출
#   - snapshot_game_time() -> str | None : 현재 인게임 시간 조회
#   - restore_game_time(time_str: str) -> None : 인게임 시간 복원
#   - inject_time_theme(hour: int | None, for_id: str | None = None) -> None : TimeTheme 엘리먼트 발행
# ================================

import re
from datetime import datetime

import chainlit as cl

from src.core.database import async_driver


def hour_from_time_string(time_str: str | None) -> int | None:
    """DB에 저장된 인게임 시간 문자열에서 시각(0-23)을 추출합니다."""
    if not time_str:
        return None
    try:
        return datetime.fromisoformat(time_str).hour
    except ValueError:
        match = re.search(r"\b([01]?\d|2[0-3]):\d{2}", time_str)
        return int(match.group(1)) if match else None


async def snapshot_game_time() -> str | None:
    """현재 인게임 시간을 ISO 문자열로 반환합니다."""
    try:
        async with async_driver.session() as session:
            rec = await session.run(
                "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS t"
            )
            row = await rec.single()
            return row["t"] if row else None
    except Exception:
        return None


async def restore_game_time(time_str: str) -> None:
    """리롤 시 Manager가 진행시킨 인게임 시간을 되돌립니다."""
    try:
        # KuzuDB SET + $param 버그 우회 — time_plan.py와 동일한 리터럴 삽입 방식 사용
        _safe = time_str.replace("\\", "\\\\").replace("'", "\\'")
        async with async_driver.session() as session:
            await session.run(
                f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentTime = '{_safe}'"
            )
    except Exception:
        pass


async def inject_time_theme(hour: int | None, for_id: str | None = None) -> None:
    """시각이 변경된 경우에만 TimeTheme 커스텀 엘리먼트를 발행합니다."""
    if hour is None:
        return
    last = cl.user_session.get("last_theme_hour", -1)
    if hour == last:
        return
    cl.user_session.set("last_theme_hour", hour)
    await cl.CustomElement(name="TimeTheme", props={"hour": hour}).send(for_id or "")
