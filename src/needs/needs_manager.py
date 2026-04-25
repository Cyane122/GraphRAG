"""
매 턴 time_manager 이후 호출되는 욕구 업데이트 루프.

처리 순서:
  1. PC를 제외한 모든 NPC 목록 조회
  2. 각 NPC의 현재 욕구 + 트레이트 로드
  3. elapsed_minutes 기반 욕구 수치 계산
  4. 초과 횟수별 분기:
       0회 → 수치만 갱신
       1회 → action_resolver (Haiku) → Event 생성
       2회↑ → 조용히 정산 (대화 중 언급 시 Sonnet 소급 생성은 manager_agent 담당)
  5. Libido: 행동 생성 없음, hint dict 반환
  6. Safety: 이벤트 decay만 계산
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from src.utils.db_utils import async_driver, update_dynamic_state
from src.needs.traits_initializer import ensure_traits
from src.needs.action_resolver import resolve_action, SETTLE_LEVELS

# ════════════════════════════════════════════════════════════
# 상수
# ════════════════════════════════════════════════════════════

THRESHOLD = 0.8

# 기본 증가율 (per minute)
NEED_BASE_RATES: dict[str, float] = {
    "hunger": 0.0033,    # 0.8 도달 ≈ 4시간
    "rest":   0.0011,    # 0.8 도달 ≈ 12시간
    "social": 0.00035,   # 0.8 도달 ≈ 38시간
    "fun":    0.00069,   # 0.8 도달 ≈ 19시간
    "safety": 0.001,     # 이벤트 기반 위주; 느린 기본 증가
    "libido": 0.00017,   # 0.8 도달 ≈ 78시간
}

# 욕구 초기 기본값 (NeedsState 최초 생성 시)
NEED_DEFAULTS: dict[str, float] = {
    "hunger": 0.3,
    "rest":   0.2,
    "social": 0.1,
    "fun":    0.4,
    "safety": 0.05,
    "libido": 0.2,
}

# 자율행동 생성 대상 욕구 (Libido / Safety 제외)
AUTONOMOUS_NEEDS = {"hunger", "rest", "social", "fun"}


# ════════════════════════════════════════════════════════════
# 퍼블릭 진입점
# ════════════════════════════════════════════════════════════

async def run_needs_update(
    pc_id:           str,
    elapsed_minutes: float,
    current_time:    datetime,
) -> dict:
    """
    app.py에서 time_manager 직후 호출.

    Returns:
        {
            "libido_hints":  {npc_id: hint_str},   # Actor 프롬프트 주입용
            "events_created": [event_id, ...]
        }
    """
    if elapsed_minutes <= 0:
        return {"libido_hints": {}, "events_created": []}

    npcs = await _fetch_all_npcs(exclude_id=pc_id)
    libido_hints: dict[str, str] = {}
    events_created: list[str] = []

    for npc in npcs:
        npc_id  = npc["id"]
        traits  = await ensure_traits(npc_id)
        needs   = await _fetch_needs(npc_id)
        profile = await _fetch_profile_props(npc_id)

        # libido_excluded 플래그 확인
        if profile.get("libido_excluded", False):
            continue

        updates: dict[str, float] = {}

        for need_name, base_rate in NEED_BASE_RATES.items():
            old_val = needs.get(need_name, NEED_DEFAULTS[need_name])

            if need_name == "safety":
                # Safety는 event decay 전용으로 별도 처리
                new_val = await _apply_safety_decay(npc_id, old_val, elapsed_minutes)
                updates[need_name] = new_val
                continue

            multiplier   = _calc_multiplier(need_name, traits, needs, profile)
            eff_rate     = base_rate * multiplier
            overflow_cnt, settled_val = _count_overflows(old_val, elapsed_minutes, eff_rate)

            if need_name == "libido":
                # Libido: 행동 생성 없음, hint만
                new_val = min(1.0, old_val + eff_rate * elapsed_minutes) if overflow_cnt == 0 else min(1.0, settled_val + 0.1)
                if overflow_cnt >= 1:
                    hint = _build_libido_hint(npc_id, profile, needs, traits)
                    if hint:
                        libido_hints[npc_id] = hint
                updates[need_name] = round(new_val, 4)
                continue

            if overflow_cnt == 0:
                # 미초과 — 수치만 갱신
                updates[need_name] = round(min(1.0, old_val + eff_rate * elapsed_minutes), 4)

            elif overflow_cnt == 1 and need_name in AUTONOMOUS_NEEDS:
                # 1회 초과 — Haiku 행동 결정
                overflow_time = current_time - timedelta(
                    minutes=(elapsed_minutes - (THRESHOLD - old_val) / eff_rate)
                )
                personality = profile.get("personality", "")
                sexual_tendency = profile.get("sexual_tendency", [])
                result = await resolve_action(
                    npc_id, need_name, overflow_time,
                    needs.get("location_id", "unknown"),
                    personality, traits, sexual_tendency,
                )
                if result:
                    events_created.append(result["event_id"])
                # 해소 후 남은 시간만큼 다시 증가
                time_after_resolve = elapsed_minutes - (THRESHOLD - old_val) / eff_rate - result.get("duration_minutes", 0) if result else 0
                settle = SETTLE_LEVELS.get(need_name, 0.2)
                new_val = min(1.0, settle + eff_rate * max(0, time_after_resolve))
                updates[need_name] = round(new_val, 4)

            else:
                # 2회↑ 초과 — 조용히 정산
                updates[need_name] = round(settled_val, 4)

        await _write_needs(npc_id, updates)

    return {"libido_hints": libido_hints, "events_created": events_created}


# ════════════════════════════════════════════════════════════
# 욕구 수치 계산
# ════════════════════════════════════════════════════════════

def _count_overflows(
    old_val:       float,
    elapsed_min:   float,
    effective_rate: float,
) -> tuple[int, float]:
    """
    elapsed_min 동안 욕구가 THRESHOLD를 몇 번 초과했는지 계산.
    반환: (초과 횟수, 마지막 정산 후 현재 수치 추정값)
    """
    if effective_rate <= 0:
        return 0, old_val

    # 첫 번째 초과까지 걸리는 시간
    time_to_first = (THRESHOLD - old_val) / effective_rate

    if elapsed_min < time_to_first:
        # 미초과
        return 0, min(1.0, old_val + effective_rate * elapsed_min)

    # 1회 이상 초과
    remaining_after_first = elapsed_min - time_to_first
    cycle_time = THRESHOLD / effective_rate  # 0 → 0.8 한 사이클
    additional_overflows = int(remaining_after_first / cycle_time)
    overflows = 1 + additional_overflows

    # 마지막 초과 이후 경과 시간 내에서 현재 수치
    time_in_last_cycle = remaining_after_first - additional_overflows * cycle_time
    settle_base = 0.2  # 해소 후 안착 추정 기준
    settled_val = min(1.0, settle_base + effective_rate * time_in_last_cycle)

    return overflows, settled_val


def _calc_multiplier(
    need:    str,
    traits:  dict,
    needs:   dict,
    profile: dict,
) -> float:
    """트레이트 + 현재 상태 기반 욕구 증가 속도 multiplier 계산."""
    t = traits

    if need == "hunger":
        m = 1.0
        m += t.get("trait_gluttony", 0) * 0.4        # 식탐 1.0 → ×1.4
        physical = needs.get("physical_condition", "healthy")
        if physical in ("healthy",):
            m *= 1.0
        return max(0.3, m)

    elif need == "rest":
        m = 1.0
        m += t.get("trait_laziness", 0) * 0.5        # 게으름 1.0 → ×1.5
        m += t.get("trait_vitality", 0) * -0.35      # 체력 1.0 → ×0.65
        m += t.get("trait_light_sleeper", 0) * 0.3   # 얕은잠 1.0 → ×1.3
        physical = needs.get("physical_condition", "healthy")
        if physical in ("injured", "ill", "hospitalized"):
            m *= 1.4
        return max(0.3, m)

    elif need == "social":
        m = 1.0
        m += t.get("trait_extroversion", 0) * 1.0    # 외향성 1.0 → ×2.0
        m += t.get("trait_attention_seeking", 0) * 0.6
        m += t.get("trait_independence", 0) * -0.4   # 독립심 높으면 감소
        return max(0.2, m)

    elif need == "fun":
        m = 1.0
        m += t.get("trait_hedonism", 0) * 0.7        # 쾌락주의 1.0 → ×1.7
        m += t.get("trait_curiosity", 0) * 0.4       # 호기심 1.0 → ×1.4
        stress = needs.get("stress_level", 0)
        if stress >= 7:
            m *= 0.5    # 극심한 스트레스 → fun 느끼기 어려움
        return max(0.2, m)

    elif need == "safety":
        m = 1.0
        m += t.get("trait_anxiety_prone", 0) * 0.5   # 불안 경향 1.0 → ×1.5
        mental = needs.get("mental_condition", "stable")
        if mental in ("stressed", "anxious"):
            m *= 1.3
        return max(0.5, m)

    elif need == "libido":
        m = 1.0
        m += t.get("trait_libido_drive", 0) * 1.0    # 성욕 강도 1.0 → ×2.0
        m += t.get("trait_hedonism", 0) * 0.4
        m += t.get("trait_intimacy_drive", 0) * 0.3
        cycle_day = needs.get("cycle_day", 0)
        if 12 <= cycle_day <= 16:                     # 배란기 ×1.8
            m *= 1.8
        physical = needs.get("physical_condition", "healthy")
        if physical in ("fatigued", "injured"):
            m *= 0.4
        return max(0.1, m)

    return 1.0


# ════════════════════════════════════════════════════════════
# Safety decay
# ════════════════════════════════════════════════════════════

async def _apply_safety_decay(
    npc_id:        str,
    old_safety:    float,
    elapsed_min:   float,
) -> float:
    """
    미해소 Event의 safety_impact × decay_rate 합산으로 Safety 재계산.
    Safety = base(0.05) + Σ[ impact × max(0, 1 - decay_rate × elapsed) ]
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event)
            WHERE e.safety_impact > 0 AND e.safety_resolved = false
            RETURN e.safety_impact    AS impact,
                   e.safety_decay_rate AS decay_rate
        """, cid=npc_id)
        rows = await rec.data()

    total = 0.05  # base safety
    for row in rows:
        impact     = row["impact"] or 0.0
        decay_rate = row["decay_rate"] or 0.002
        residual   = max(0.0, 1.0 - decay_rate * elapsed_min)
        total     += impact * residual

    return round(min(1.0, total), 4)


# ════════════════════════════════════════════════════════════
# Libido hint 생성
# ════════════════════════════════════════════════════════════

def _build_libido_hint(
    npc_id:  str,
    profile: dict,
    needs:   dict,
    traits:  dict,
) -> Optional[str]:
    """
    Libido 0.8 초과 시 Actor 프롬프트에 주입할 hint 문자열 반환.
    행동 이벤트 생성 없음.
    """
    tendency = profile.get("sexual_tendency", [])

    # repressed → 행동 억제, Stress 상승 (DynamicState 업데이트는 호출부에서)
    if "repressed" in tendency:
        return f"[NEEDS_HINT:{npc_id}] Libido is suppressed — increases sensitivity and visible tension. Do NOT depict resolution."

    location_id     = needs.get("location_id", "")
    partner_id      = profile.get("libido_partner", "")

    # 대략적인 privacy 판단 (location_id 문자열 기반)
    if "villa" in location_id or "home" in location_id or "205" in location_id:
        privacy = "private"
    elif "bathroom" in location_id or "restroom" in location_id:
        privacy = "semi-private"
    else:
        privacy = "public"

    # 파트너 존재 여부는 DB 쿼리 없이 partner_id 필드로만 체크
    # (정확한 위치 공유 여부는 Actor가 context에서 판단)
    has_partner = bool(partner_id)

    if privacy == "private":
        if has_partner:
            hint = "initiate_intimacy — body language: lingering gaze, casual touch, proximity"
        else:
            hint = "solo_relief — brief withdrawal, sounds from another room"
    elif privacy == "semi-private":
        if "exhibitionism" in tendency or "light_exhibitionism" in tendency:
            hint = "exhibitionism_urge — small daring gesture, checking if observed"
        else:
            hint = "seek_private_space — restless, distracted, excuses self"
    else:  # public
        if "exhibitionism" in tendency:
            hint = "exhibitionism_urge — subtle but deliberate exposure gesture"
        elif has_partner:
            hint = "suppress + think_of_partner — distracted eye contact / brief touch"
        else:
            hint = "suppress — heightened sensory awareness, brief distraction"

    return f"[NEEDS_HINT:{npc_id}] Libido 0.8+. Behavior hint: {hint}. Do NOT narrate the need explicitly."


# ════════════════════════════════════════════════════════════
# DB 읽기 / 쓰기
# ════════════════════════════════════════════════════════════

async def _fetch_all_npcs(exclude_id: str) -> list[dict]:
    """libido_excluded=false인 모든 NPC (PC 제외) 반환."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.id <> $exclude
            OPTIONAL MATCH (c)-[:HAS_PROFILE]->(sp:StaticProfile)
            WHERE sp.libido_excluded IS NULL OR sp.libido_excluded = false
            RETURN c.id AS id
        """, exclude=exclude_id)
        rows = await rec.data()
    return [{"id": r["id"]} for r in rows if r["id"]]


async def _fetch_needs(npc_id: str) -> dict:
    """
    NPC의 현재 욕구 수치 딕셔너리 반환.
    DynamicState → NeedsState 순으로 탐색.
    없으면 NeedsState 노드 자동 생성.
    """
    async with async_driver.session() as session:
        # DynamicState 우선
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN properties(d) AS props
        """, cid=npc_id)
        row = await rec.single()
        if row and row["props"]:
            return dict(row["props"])

        # NeedsState
        rec2 = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN properties(n) AS props
        """, cid=npc_id)
        row2 = await rec2.single()
        if row2 and row2["props"]:
            return dict(row2["props"])

        # 없으면 NeedsState 생성
        defaults = {f: v for f, v in NEED_DEFAULTS.items()}
        defaults["id"] = f"{npc_id}_needs"
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_NEEDS]->(n:NeedsState $props)
        """, cid=npc_id, props=defaults)
        return defaults


async def _fetch_profile_props(npc_id: str) -> dict:
    """StaticProfile 속성 반환 (sexual_tendency, libido_* 등)."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN properties(sp) AS props
        """, cid=npc_id)
        row = await rec.single()
        return dict(row["props"]) if row and row["props"] else {}


async def _write_needs(npc_id: str, updates: dict) -> None:
    """욕구 수치를 DynamicState 또는 NeedsState에 저장."""
    if not updates:
        return

    # 욕구 필드만 필터 (다른 DynamicState 필드 건드리지 않음)
    need_keys = set(NEED_BASE_RATES.keys())
    need_updates = {k: v for k, v in updates.items() if k in need_keys}
    if not need_updates:
        return

    async with async_driver.session() as session:
        # DynamicState 있으면 거기에
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d.id AS did
        """, cid=npc_id)
        row = await rec.single()

    if row:
        await update_dynamic_state(npc_id, need_updates)
        return

    # NeedsState
    set_clause = ", ".join(f"n.{k} = ${k}" for k in need_updates)
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (c:Character {{id: $cid}})-[:HAS_NEEDS]->(n:NeedsState) SET {set_clause}",
            cid=npc_id, **need_updates,
        )