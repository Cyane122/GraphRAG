# ================================
# src/simulation/systems/organic.py
#
# 임신 확률 계산 및 생리 주기/임신 상태 관리를 담당합니다.
#
# Functions
#   - detect_internal_ejaculation(actor_response: str) -> bool : regex pre-filter 후 Flash로 질내·콘돔 여부 분류
#   - process_ejaculation(npc_id: str, actor_response: str, scene_char_ids: list[str] | None, intimate_char_ids: list[str] | None) -> str | None : 질내사정 감지 시 확률 계산 후 임신 여부 결정
#   - set_pregnant_manual(char_ref: str) -> str | None : 이름/ID로 캐릭터를 직접 임신 상태로 전환 (수동 보정용)
#   - tick_pregnancy_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 pregnancy_day 증가
#   - tick_cycle_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 cycle_day 증가 (28일 주기)
#   - tick_all_cycles(days_passed: int) -> None : has_menstrual_cycle=true인 모든 캐릭터 일괄 갱신
# ================================

import random
import re
from datetime import datetime

from src.config import MODEL_CLASSIFIER as _FLASH_MODEL
from src.core.database import async_driver, update_dynamic_state
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text

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

# ── 사정·절정 표현 pre-filter (LLM 호출 전 빠른 제외) ──────
# 직접 표현뿐 아니라 간접 절정 묘사·콘돔 파열도 포함해 false negative를 최소화한다.
_EJAC_RE = re.compile(
    # 직접 사정 표현
    r"질내사정"
    r"|(?:안|속)에\s*(?:쏟|싸|쌌|뿌렸|채웠|사정)"
    r"|자궁\s*(?:안|속)에\s*(?:쏟|싸|뿌렸|사정)"
    r"|사정\s*(?:했|해버렸|해\s*버렸|하고\s*말았|하며)"
    r"|뿌렸다|뿌렸어|쏟아냈다|쏟아졌다"
    # 절정·클라이막스 (간접 묘사)
    r"|절정\s*(?:에\s*달했|을\s*맞이|했|에\s*이르|을\s*느)"
    r"|클라이막스"
    r"|(?:쾌감|욕구|충동).*통제할\s*수\s*없"
    # 콘돔·고무 파열과 함께하는 삽입 묘사
    r"|(?:찢어진|터진|파열된)\s*(?:라텍스|콘돔|고무)"
    r"|(?:라텍스|콘돔|고무\s*막)\s*(?:찢어지|터지|파열)"
)

_EJAC_CLASSIFY_SYSTEM = """\
You are a scene classifier for a Korean adult roleplay system.
Analyze the ejaculation scene and return JSON only — no explanation, no markdown.

vaginal: true if ejaculation occurred during vaginal penetration.
         false if during anal penetration (항문 / 애널 / 후장).
condom_protected: true if a condom was worn and there is NO mention of it breaking,
                  slipping off, or being absent (생으로 / 콘돔 없이 / 파열 / 찢어짐 등).
                  false otherwise (no condom, condom broke, bareback).

Output exactly: {"vaginal": true, "condom_protected": false}"""


def _calc_prob(cycle_day: int, count: int) -> float:
    """누적 확률 계산.
    p = 1 - (1 - base * weight)^count, capped at PROB_CAP.
    """
    if 10 <= cycle_day <= 17:
        weight = DAY_WEIGHT.get(cycle_day, 0.1)
        base   = BASE_FERTILE * weight
    else:
        base   = BASE_INFERTILE

    return min(1 - (1 - base) ** count, PROB_CAP)


async def _classify_ejaculation(actor_response: str) -> dict:
    """Flash로 사정 맥락(질내 여부·콘돔 상태)을 분류한다.

    실패 시 빈 dict 반환 → 호출부에서 fallback 처리.
    """
    model = get_model(model_name=_FLASH_MODEL, system_prompt=_EJAC_CLASSIFY_SYSTEM)
    try:
        response = await model.generate_content_async(
            actor_response[:3000],
            generation_config={
                "max_output_tokens": 32,
                "temperature": 0.0,
                "response_mime_type": "application/json",
            },
        )
        return extract_json_from_llm(get_response_text(response), source="ejac_classifier") or {}
    except Exception as e:
        print(f"[PregnancyMgr] ejaculation classify failed: {e}")
        return {}


async def detect_internal_ejaculation(actor_response: str) -> bool:
    """임신 가능한 질내사정 여부를 감지한다.

    1단계: regex pre-filter — 사정 표현 없으면 즉시 False
    2단계: Flash 분류 — 질내 여부·콘돔 상태 판단
    Flash 실패 시 임신 가능으로 처리(False negative 방지).
    """
    if not _EJAC_RE.search(actor_response):
        return False

    result = await _classify_ejaculation(actor_response)
    vaginal          = result.get("vaginal")
    condom_protected = result.get("condom_protected")

    print(f"[PregnancyMgr] ejac classify → vaginal={vaginal} condom_protected={condom_protected}")

    # Flash 실패(빈 dict) → 사정 표현이 있으니 임신 가능으로 fallback
    if vaginal is None:
        return True
    if not vaginal:
        return False
    if condom_protected:
        return False
    return True


async def _get_char_name(char_id: str) -> str:
    """Character 노드에서 이름을 조회합니다. ID 또는 한글 이름 모두 허용."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character) WHERE c.id = $cid OR c.name = $cid RETURN c.name AS name",
            cid=char_id,
        )
        row = await rec.single()
    return (row["name"] if row else None) or char_id


async def _resolve_char_id(name_or_id: str) -> str | None:
    """이름 또는 ID로 실제 Character.id를 반환합니다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character) WHERE c.id = $ref OR c.name = $ref RETURN c.id AS cid",
            ref=name_or_id,
        )
        row = await rec.single()
    return row["cid"] if row else None


async def _get_cycle_state(npc_id: str) -> dict:
    """DynamicState에서 임신/주기 관련 필드 조회. ID 또는 한글 이름 모두 허용."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE c.id = $cid OR c.name = $cid
            RETURN d.cycle_day             AS cycle_day,
                   d.pregnant              AS pregnant,
                   d.pregnancy_day         AS pregnancy_day,
                   d.cum_shots_this_cycle  AS cum_shots,
                   d.has_menstrual_cycle   AS has_menstrual_cycle
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            return {"cycle_day": 1, "pregnant": False, "pregnancy_day": 0, "cum_shots": 0, "has_menstrual_cycle": False}
        raw_cycle = row["has_menstrual_cycle"]
        return {
            "cycle_day":           int(row["cycle_day"]    or 1),
            "pregnant":            bool(row["pregnant"]    or False),
            "pregnancy_day":       int(row["pregnancy_day"] or 0),
            "cum_shots":           int(row["cum_shots"]    or 0),
            "has_menstrual_cycle": False if raw_cycle is None else bool(raw_cycle),
        }


async def process_ejaculation(
    npc_id: str,
    actor_response: str,
    scene_char_ids: list[str] | None = None,
    intimate_char_ids: list[str] | None = None,
) -> str | None:
    """
    질내사정 감지 시 실제 성행위에 참여한 NPC만 임신 확률 계산.

    intimate_char_ids: 실제 성행위 참여자 명시 목록 (없으면 npc_id만 처리).
    scene_char_ids: 씬 존재 캐릭터 전체 — 임신 계산에는 사용하지 않음.
    Returns:
        임신 확정 시 OOC 메시지 문자열, 아니면 None.
    """
    if not await detect_internal_ejaculation(actor_response):
        return None

    # 명시된 intimate 참여자가 있으면 그것만, 없으면 npc_id 단독
    # None이 아닌 빈 리스트([])도 "참여자 없음"이 아닌 "조회 실패"이므로 None만 fallback 트리거
    seen: set[str] = set()
    candidates: list[str] = []
    for cid in (intimate_char_ids if intimate_char_ids is not None else [npc_id]):
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


async def set_pregnant_manual(char_ref: str) -> str | None:
    """
    이름 또는 ID로 캐릭터를 직접 임신 상태로 전환합니다.

    자동 감지가 실패했을 때 /임신 커맨드에서 호출하는 수동 보정용.
    Returns:
        OOC 확인 메시지 문자열, 캐릭터를 찾지 못하면 None.
    """
    char_id = await _resolve_char_id(char_ref)
    if not char_id:
        return None

    state = await _get_cycle_state(char_id)
    if state["pregnant"]:
        char_name = await _get_char_name(char_id)
        return f"*[시스템] {char_name}은(는) 이미 임신 중입니다. (임신 {state['pregnancy_day']}일째)*"

    await update_dynamic_state(char_id, {
        "pregnant":             True,
        "pregnancy_day":        1,
        "cum_shots_this_cycle": 0,
    })

    state = await _get_cycle_state(char_id)
    char_name = await _get_char_name(char_id)
    ooc_msg = (
        f"*[시스템] {char_name}이(가) 임신했습니다. (임신 1일째) "
        f"수동 설정 — 가임기 {state['cycle_day']}일째. "
        f"임신 13주(91일) 이후 안정기 진입.*"
    )
    print(f"[PregnancyMgr] {char_id} 수동 임신 설정 완료")
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
    # days_passed >= 28이면 날짜 비교와 무관하게 완전한 주기가 경과했으므로 반드시 리셋
    if new_cycle_day < state["cycle_day"] or days_passed >= 28:
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
            # days_passed >= 28이면 날짜 비교와 무관하게 완전한 주기가 경과했으므로 반드시 리셋
            if new_cycle_day < cycle_day or days_passed >= 28:
                updates["cum_shots_this_cycle"] = 0
            await update_dynamic_state(char_id, updates)
            print(f"[PregnancyMgr] {char_id} cycle_day {cycle_day} → {new_cycle_day}")
