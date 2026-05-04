"""
임신 확률 계산 및 임신 상태 관리.

가임기(cycle_day 10–17) 내 질내사정 감지 시 누적 확률로 임신 여부 결정.
공식: p = 1 - (1 - BASE * day_weight)^count, cap=0.45
임신 확정 시: DynamicState 갱신 + pending_ooc 세션 플래그 설정

cycle_day 단계:
  1– 5 : 생리 중   (비가임기)
  6– 9 : 난포기    (비가임기)
  10–17: 가임기    (임신 가능)
  18–28: 황체기    (비가임기)

pregnancy_day:
  0  : 미임신
  1+ : 임신 N일째
  안정기(13주+ = 91일+): 업무 수행 가능
"""

import random
import re
from datetime import datetime

from src.utils.db_utils import async_driver, update_dynamic_state

# ── 확률 파라미터 ─────────────────────────────────────────
BASE_FERTILE    = 0.27   # 가임기 단발 기준 확률
BASE_INFERTILE  = 0.01   # 비가임기 희박 확률
PROB_CAP        = 0.45   # 한 주기 최대 임신 확률

DAY_WEIGHT: dict[int, float] = {
    10: 0.30,
    11: 0.50,
    12: 0.70,
    13: 0.90,
    14: 1.00,  # 배란 피크
    15: 0.80,
    16: 0.30,
    17: 0.10,
}

# ── 질내사정 감지 패턴 ────────────────────────────────────
_INTERNAL_EJAC_RE = re.compile(
    r"질내사정|안에\s*(?:쏟|싸|쌌|뿌렸|채웠)|속에\s*(?:쏟|싸|뿌렸)|"
    r"자궁\s*(?:안|속)에\s*(?:쏟|싸|뿌렸)|뿌렸다|뿌렸어"
)


def _calc_prob(cycle_day: int, count: int) -> float:
    """
    누적 확률 계산.
    p = 1 - (1 - base * weight)^count, capped at PROB_CAP.
    """
    if 10 <= cycle_day <= 17:
        weight = DAY_WEIGHT.get(cycle_day, 0.1)
        base   = BASE_FERTILE * weight
    else:
        base   = BASE_INFERTILE

    p = 1 - (1 - base) ** count
    return min(p, PROB_CAP)


def detect_internal_ejaculation(actor_response: str) -> bool:
    """actor_response에서 질내사정 여부를 감지한다."""
    return bool(_INTERNAL_EJAC_RE.search(actor_response))


async def _get_cycle_state(npc_id: str) -> dict:
    """DynamicState에서 임신/주기 관련 필드 조회."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d.cycle_day             AS cycle_day,
                   d.pregnant              AS pregnant,
                   d.pregnancy_day         AS pregnancy_day,
                   d.cum_shots_this_cycle  AS cum_shots
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            return {"cycle_day": 1, "pregnant": False, "pregnancy_day": 0, "cum_shots": 0}
        return {
            "cycle_day":    int(row["cycle_day"]    or 1),
            "pregnant":     bool(row["pregnant"]    or False),
            "pregnancy_day": int(row["pregnancy_day"] or 0),
            "cum_shots":    int(row["cum_shots"]    or 0),
        }


async def process_ejaculation(npc_id: str, actor_response: str) -> str | None:
    """
    질내사정 감지 시 확률 계산 후 임신 여부 결정.

    Returns:
        임신 확정 시 OOC 메시지 문자열, 아니면 None.
    """
    if not detect_internal_ejaculation(actor_response):
        return None

    state = await _get_cycle_state(npc_id)

    if state["pregnant"]:
        return None  # 이미 임신 중

    new_count = state["cum_shots"] + 1
    cycle_day = state["cycle_day"]

    # 사정 횟수 누적 저장
    await update_dynamic_state(npc_id, {"cum_shots_this_cycle": new_count})

    prob = _calc_prob(cycle_day, new_count)
    roll = random.random()

    print(
        f"[PregnancyMgr] cycle_day={cycle_day} | shots={new_count} | "
        f"prob={prob:.1%} | roll={roll:.3f} | {'임신!' if roll < prob else '미임신'}"
    )

    if roll >= prob:
        return None

    # ── 임신 확정 ──────────────────────────────────────────
    await update_dynamic_state(npc_id, {
        "pregnant":             True,
        "pregnancy_day":        1,
        "cum_shots_this_cycle": 0,   # 주기 카운터 초기화
    })

    ooc_msg = (
        f"*[시스템] 강하늘이 임신했습니다. (임신 1일째) "
        f"가임기 {cycle_day}일째, 질내사정 {new_count}회 누적. "
        f"임신 13주(91일) 이후 안정기 진입 시 업무 수행 가능.*"
    )
    print(f"[PregnancyMgr] 임신 확정 → OOC 주입 예약")
    return ooc_msg


async def tick_pregnancy_day(npc_id: str, days_passed: int) -> None:
    """
    게임 내 날짜 경과 시 pregnancy_day 증가.
    manager_agent에서 days_passed > 0 일 때 호출.
    """
    if days_passed <= 0:
        return

    state = await _get_cycle_state(npc_id)
    if not state["pregnant"]:
        return

    new_day = state["pregnancy_day"] + days_passed
    await update_dynamic_state(npc_id, {"pregnancy_day": new_day})

    trimester = "안정기" if new_day >= 91 else ("초기" if new_day < 42 else "중기")
    print(f"[PregnancyMgr] 임신 {new_day}일째 ({trimester})")


async def tick_cycle_day(npc_id: str, days_passed: int) -> None:
    """
    게임 내 날짜 경과 시 cycle_day 증가 (28일 주기).
    임신 중에는 cycle_day 틱 스킵.
    """
    if days_passed <= 0:
        return

    state = await _get_cycle_state(npc_id)

    if state["pregnant"]:
        await tick_pregnancy_day(npc_id, days_passed)
        return

    new_cycle_day = ((state["cycle_day"] - 1 + days_passed) % 28) + 1
    updates: dict = {"cycle_day": new_cycle_day}

    # 새 주기 시작 시 사정 카운터 초기화
    if new_cycle_day < state["cycle_day"]:
        updates["cum_shots_this_cycle"] = 0

    await update_dynamic_state(npc_id, updates)
    print(f"[PregnancyMgr] cycle_day → {new_cycle_day}")