# ================================
# src/simulation/events/evaluator.py
#
# StaticEvent 조건 평가 함수들.
# time / stat / flag 세 가지 조건 타입을 평가합니다.
#
# Functions
#   - evaluate_conditions(conditions: list[dict], current_dt: datetime) -> bool : 조건 목록을 AND로 평가합니다.
# ================================

import json
import operator as _op
from datetime import datetime

from src.core.database.driver import async_driver

_OPS: dict = {
    ">=": _op.ge,
    ">":  _op.gt,
    "<=": _op.le,
    "<":  _op.lt,
    "==": _op.eq,
}


async def _eval_time(cond: dict, current_dt: datetime) -> bool:
    """게임 날짜(월·일)와 조건값(MM-DD)을 정수로 변환해 비교합니다."""
    value = cond.get("value", "")
    fn    = _OPS.get(cond.get("op", ">="))
    if not fn or not value:
        return False

    try:
        cond_month, cond_day = (int(x) for x in value.split("-"))
        cond_mmdd = cond_month * 100 + cond_day
        curr_mmdd = current_dt.month * 100 + current_dt.day
        return fn(curr_mmdd, cond_mmdd)
    except (ValueError, AttributeError):
        return False


async def _eval_stat(cond: dict, _current_dt: datetime) -> bool:
    """두 캐릭터 간 RELATIONSHIP 엣지의 수치 필드를 비교합니다."""
    field     = cond.get("field")
    op        = cond.get("op", ">=")
    value     = cond.get("value")
    char_from = cond.get("from")
    char_to   = cond.get("to")

    fn = _OPS.get(op)
    if not all([fn, field, value is not None, char_from, char_to]):
        return False

    async with async_driver.session() as session:
        rec = await session.run(
            f"MATCH (a:Character {{id: $a}})-[r:RELATIONSHIP]->(b:Character {{id: $b}}) RETURN r.{field} AS val",
            a=char_from,
            b=char_to,
        )
        row = await rec.single()

    if not row or row.get("val") is None:
        return False

    try:
        return fn(int(row["val"]), int(value))
    except (TypeError, ValueError):
        return False


async def _eval_flag(cond: dict, _current_dt: datetime) -> bool:
    """GlobalState.flags JSON에서 boolean 플래그를 조회합니다."""
    key = cond.get("key")
    if not key:
        return False

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.flags AS flags"
        )
        row = await rec.single()

    if not row or not row.get("flags"):
        return False

    try:
        flags = json.loads(row["flags"])
    except (json.JSONDecodeError, TypeError):
        return False

    return bool(flags.get(key, False))


_EVAL_MAP = {
    "time": _eval_time,
    "stat": _eval_stat,
    "flag": _eval_flag,
}


async def evaluate_conditions(conditions: list[dict], current_dt: datetime) -> bool:
    """
    조건 목록을 AND로 평가합니다.
    빈 목록은 False를 반환합니다 (명시적 조건 없는 이벤트는 자동 발화하지 않음).
    """
    if not conditions:
        return False

    for cond in conditions:
        evaluator = _EVAL_MAP.get(cond.get("type"))
        if evaluator is None:
            return False
        if not await evaluator(cond, current_dt):
            return False

    return True
