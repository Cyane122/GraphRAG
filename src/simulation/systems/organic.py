# ================================
# src/simulation/systems/organic.py
#
# 임신 확률 계산 및 생리 주기/임신 상태 관리를 담당합니다.
#
# Functions
#   - detect_internal_ejaculation(actor_response: str) -> bool : actor_response에서 질내사정 여부 감지
#   - process_ejaculation(npc_id: str, actor_response: str) -> str | None : 질내사정 감지 시 확률 계산 후 임신 여부 결정
#   - tick_pregnancy_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 pregnancy_day 증가
#   - tick_cycle_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 cycle_day 증가 (28일 주기)
# ================================

import random
import re
from datetime import datetime

from src.core.database import async_driver, update_dynamic_state

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


async def _get_char_name(char_id: str) -> str:
    """Character 노드에서 이름을 조회합니다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {id: $cid}) RETURN c.name AS name", cid=char_id
        )
        row = await rec.single()
    return (row["name"] if row else None) or char_id


async def _get_cycle_state(npc_id: str) -> dict:
    """DynamicState에서 임신/주기 관련 필드 조회."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d.cycle_day             AS cycle_day,
                   d.pregnant              AS pregnant,
                   d.pregnancy_day         AS pregnancy_day,
                   d.cum_shots_this_cycle  AS cum_shots,
                   d.has_menstrual_cycle   AS has_menstrual_cycle
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            return {"cycle_day": 1, "pregnant": False, "pregnancy_day": 0, "cum_shots": 0, "has_menstrual_cycle": True}
        raw_cycle = row["has_menstrual_cycle"]
        return {
            "cycle_day":           int(row["cycle_day"]    or 1),
            "pregnant":            bool(row["pregnant"]    or False),
            "pregnancy_day":       int(row["pregnancy_day"] or 0),
            "cum_shots":           int(row["cum_shots"]    or 0),
            "has_menstrual_cycle": True if raw_cycle is None else bool(raw_cycle),
        }


async def process_ejaculation(
    npc_id: str,
    actor_response: str,
    scene_char_ids: list[str] | None = None,
) -> str | None:
    """
    질내사정 감지 시 씬에 등장한 생리주기 있는 NPC를 대상으로 확률 계산 후 임신 여부 결정.

    scene_char_ids가 있으면 해당 목록을 우선 처리하고, npc_id를 fallback으로 추가합니다.
    Returns:
        임신 확정 시 OOC 메시지 문자열, 아니면 None.
    """
    if not detect_internal_ejaculation(actor_response):
        return None

    # scene 캐릭터 + fallback npc_id 순으로 중복 없이 처리
    seen: set[str] = set()
    candidates: list[str] = []
    for cid in [*(scene_char_ids or []), npc_id]:
        if cid not in seen:
            seen.add(cid)
            candidates.append(cid)

    for char_id in candidates:
        state = await _get_cycle_state(char_id)
        if not state["has_menstrual_cycle"]:
            continue
        if state["pregnant"]:
            continue

        new_count = state["cum_shots"] + 1
        cycle_day = state["cycle_day"]

        await update_dynamic_state(char_id, {"cum_shots_this_cycle": new_count})

        prob = _calc_prob(cycle_day, new_count)
        roll = random.random()

        print(
            f"[PregnancyMgr] {char_id}: cycle_day={cycle_day} | shots={new_count} | "
            f"prob={prob:.1%} | roll={roll:.3f} | {'임신!' if roll < prob else '미임신'}"
        )

        if roll >= prob:
            continue

        # ── 임신 확정 ──────────────────────────────────────────
        await update_dynamic_state(char_id, {
            "pregnant":             True,
            "pregnancy_day":        1,
            "cum_shots_this_cycle": 0,
        })

        char_name = await _get_char_name(char_id)
        ooc_msg = (
            f"*[시스템] {char_name}이(가) 임신했습니다. (임신 1일째) "
            f"가임기 {cycle_day}일째, 질내사정 {new_count}회 누적. "
            f"임신 13주(91일) 이후 안정기 진입.*"
        )
        print(f"[PregnancyMgr] {char_id} 임신 확정 → OOC 주입 예약")
        return ooc_msg

    return None


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
    """단일 NPC의 cycle_day 증가. tick_all_cycles 미지원 환경용 fallback."""
    if days_passed <= 0:
        return

    state = await _get_cycle_state(npc_id)
    if not state["has_menstrual_cycle"]:
        return
    if state["pregnant"]:
        await tick_pregnancy_day(npc_id, days_passed)
        return

    new_cycle_day = ((state["cycle_day"] - 1 + days_passed) % 28) + 1
    updates: dict = {"cycle_day": new_cycle_day}
    if new_cycle_day < state["cycle_day"]:
        updates["cum_shots_this_cycle"] = 0
    await update_dynamic_state(npc_id, updates)
    print(f"[PregnancyMgr] {npc_id} cycle_day → {new_cycle_day}")


async def tick_all_cycles(days_passed: int) -> None:
    """
    날짜 경과 시 has_menstrual_cycle=true인 모든 캐릭터의 cycle_day를 일괄 갱신.
    주 NPC만 처리하던 기존 방식 대신 이 함수를 사용해야 한다.
    """
    if days_passed <= 0:
        return

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE d.has_menstrual_cycle = true
            RETURN c.id                   AS char_id,
                   d.cycle_day            AS cycle_day,
                   d.pregnant             AS pregnant,
                   d.pregnancy_day        AS pregnancy_day,
                   d.cum_shots_this_cycle AS cum_shots
        """)
        rows = await rec.data()

    for row in rows:
        char_id   = row["char_id"]
        cycle_day = int(row["cycle_day"] or 1)
        pregnant  = bool(row["pregnant"] or False)

        if pregnant:
            new_day = int(row["pregnancy_day"] or 0) + days_passed
            await update_dynamic_state(char_id, {"pregnancy_day": new_day})
            trimester = "안정기" if new_day >= 91 else ("초기" if new_day < 42 else "중기")
            print(f"[PregnancyMgr] {char_id} 임신 {new_day}일째 ({trimester})")
        else:
            new_cycle_day = ((cycle_day - 1 + days_passed) % 28) + 1
            updates: dict = {"cycle_day": new_cycle_day}
            if new_cycle_day < cycle_day:
                updates["cum_shots_this_cycle"] = 0
            await update_dynamic_state(char_id, updates)
            print(f"[PregnancyMgr] {char_id} cycle_day {cycle_day} → {new_cycle_day}")
