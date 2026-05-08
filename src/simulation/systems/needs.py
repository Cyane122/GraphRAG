# ================================
# src/simulation/systems/needs.py
#
# NPC 트레이트 초기화 및 매 턴 욕구 업데이트 루프를 담당합니다.
#
# Functions
#   - ensure_traits(char_id: str) -> dict : StaticProfile에 trait_* 필드가 없으면 LLM으로 생성 후 반환
#   - run_needs_update(pc_id: str, elapsed_minutes: float, current_time: datetime) -> dict : 매 턴 NPC 욕구 수치 갱신
# ================================

import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

from src.config import MODEL_STATE_UPDATER as TRAITS_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm
from src.simulation.state.classifier import _sanitize_stress_level
from src.agents.resolver import resolve_action, SETTLE_LEVELS

# ════════════════════════════════════════════════════════════
# 트레이트 상수
# ════════════════════════════════════════════════════════════

ALL_TRAIT_KEYS = [
    "trait_laziness", "trait_vitality", "trait_gluttony", "trait_light_sleeper",
    "trait_sensitivity", "trait_extroversion", "trait_introversion",
    "trait_attention_seeking", "trait_independence", "trait_empathy", "trait_jealousy",
    "trait_ambition", "trait_perfectionism", "trait_curiosity", "trait_control_need",
    "trait_achievement_drive", "trait_anxiety_prone",
    "trait_hedonism", "trait_impulsivity", "trait_adventurousness",
    "trait_comfort_seeking", "trait_indulgence", "trait_risk_aversion",
    "trait_attachment", "trait_possessiveness", "trait_trust", "trait_loyalty",
    "trait_intimacy_drive", "trait_dependency",
    "trait_responsibility", "trait_self_esteem", "trait_morality",
    "trait_pride", "trait_diligence", "trait_stubbornness", "trait_libido_drive",
]

REQUIRED_KEYS = [
    "trait_laziness", "trait_vitality", "trait_extroversion",
    "trait_hedonism", "trait_anxiety_prone", "trait_libido_drive",
    "trait_attachment", "trait_ambition", "trait_impulsivity",
]


# ════════════════════════════════════════════════════════════
# 트레이트 초기화 (구 traits_initializer.py)
# ════════════════════════════════════════════════════════════

def _is_traits_complete(props: dict) -> bool:
    """필수 trait 키가 모두 존재하는지 확인한다."""
    return all(k in props for k in REQUIRED_KEYS)


def _has_nonzero_traits(props: dict) -> bool:
    """저장된 trait 값 중 하나라도 실제 편향값이 있는지 확인한다."""
    return any(abs(_as_float(props.get(k), 0.0)) > 0.0001 for k in ALL_TRAIT_KEYS if k in props)


async def ensure_traits(char_id: str) -> dict:
    """
    StaticProfile (또는 DynamicState) 에 trait_* 필드가 없으면
    Haiku로 생성해 DB에 저장 후 반환.
    이미 존재하면 DB 조회 결과 그대로 반환 (LLM 호출 없음).
    """
    profile, source_label = await _load_profile(char_id)
    if not profile:
        print(f"[TraitsInit] {char_id}: 프로필 없음 → 기본값 반환")
        return _default_traits()

    if _is_traits_complete(profile) and _has_nonzero_traits(profile):
        return {k: profile[k] for k in ALL_TRAIT_KEYS if k in profile}

    personality = profile.get("personality", "")
    role        = profile.get("role", profile.get("job", ""))
    age         = profile.get("age", "")

    # personality 미정의 캐릭터는 DynamicState 행동 설명으로 보완
    if not personality and source_label == "StaticProfile":
        async with async_driver.session() as session:
            rec = await session.run("""
                MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
                RETURN d.behavioral_facade AS bf,
                       d.mood AS mood,
                       d.mental_condition AS mc
            """, cid=char_id)
            dyn_row = await rec.single()
            if dyn_row:
                parts = [v for v in (dyn_row.get("bf"), dyn_row.get("mood"), dyn_row.get("mc")) if v]
                personality = " | ".join(parts)

    print(f"[TraitsInit] {char_id}: trait_* 없음 → Haiku 생성 중...")

    generated = await _generate_traits_from_personality(char_id, personality, role, str(age))
    if not generated:
        print(f"[TraitsInit] {char_id}: 트레이트 생성 실패 → DB 저장 생략")
        return _default_traits()

    await _write_traits_to_db(char_id, source_label, generated)
    print(f"[TraitsInit] {char_id}: 트레이트 생성 완료 → DB 저장")

    return generated


async def _load_profile(char_id: str) -> tuple[dict, str]:
    """StaticProfile(JSON blob) → DynamicState 순으로 탐색. (props_dict, label) 반환.
    StaticProfile은 props 컬럼이 JSON 문자열이므로 파싱 후 반환한다.
    """
    async with async_driver.session() as session:
        # StaticProfile: props 컬럼이 JSON blob — n.props 로 직접 조회 후 파싱
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(n:StaticProfile)
            RETURN n.props AS props_json
        """, cid=char_id)
        row = await rec.single()
        if row and row["props_json"]:
            try:
                return json.loads(row["props_json"]), "StaticProfile"
            except (ValueError, TypeError):
                pass

        # DynamicState: 명시적 컬럼 노드 — 노드 전체를 dict로 변환
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(n:DynamicState)
            RETURN n AS props
        """, cid=char_id)
        row = await rec.single()
        if row and row["props"]:
            return dict(row["props"]), "DynamicState"

    return {}, ""


async def _generate_traits_from_personality(
    char_id: str, personality: str, role: str, age: str
) -> dict:
    """Haiku에 personality 문자열을 주고 trait 점수 JSON을 생성한다."""
    keys_inline = ", ".join(ALL_TRAIT_KEYS)

    system_instruction = (
        "You are a character trait analyzer. "
        "You output ONLY raw JSON — no markdown, no code fences, no explanation. "
        "Your response must start with { and end with }."
    )

    prompt = f"""Analyze the character's personality and generate their trait scores.

Character Profile:
- ID: {char_id}
- Age: {age}
- Role: {role}
- Personality Description: {personality}

Instructions for JSON output:
1. The output must be a single JSON object.
2. Include ALL of the following {len(ALL_TRAIT_KEYS)} keys. Do not omit any.
  - Keys: {keys_inline}
3. Every key's value must be a float between -1.0 and 1.0.
  - A positive value indicates a strong presence of the trait.
  - A negative value indicates the opposite tendency.
  - 0.0 means the trait is neutral.

Mapping Examples:
- "calm, logical, aloof" → "extroversion": -0.4, "impulsivity": -0.6, "control_need": 0.5
- "loud, energetic, social" → "extroversion": 0.9, "vitality": 0.8, "laziness": -0.5
- "strict, perfectionist" → "diligence": 0.9, "perfectionism": 0.9, "control_need": 0.8

Return only the raw JSON object."""

    try:
        model = get_model(TRAITS_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "max_output_tokens": 4096,
                "temperature":       0.0,
                "response_mime_type": "application/json",
            }
        )
        parsed = extract_json_from_llm(resp.text, source=f"TraitsInit:{char_id}")
        if not isinstance(parsed, dict):
            print(f"[TraitsInit] {char_id}: 파싱 결과가 dict가 아님 ({type(parsed)}) → 저장 생략")
            return {}

        result = {k: max(-1.0, min(1.0, float(v))) for k, v in parsed.items() if k in ALL_TRAIT_KEYS}
        if not result:
            print(f"[TraitsInit] {char_id}: 유효 trait 키 없음 → 저장 생략")
            return {}

        missing = [k for k in ALL_TRAIT_KEYS if k not in result]
        if missing:
            print(f"[TraitsInit] {char_id}: 누락 키 {len(missing)}개 → 0.0으로 채움")
            for k in missing:
                result[k] = 0.0

        if not all(k in result for k in REQUIRED_KEYS):
            print(f"[TraitsInit] {char_id}: 필수 키 누락, 생성 실패로 간주 → 저장 생략")
            return {}

        return result

    except Exception as e:
        print(f"[TraitsInit] Flash 생성 실패 ({char_id}): {e} → 저장 생략")
        return {}


async def _write_traits_to_db(char_id: str, source_label: str, traits: dict) -> None:
    """trait 딕셔너리를 DB에 저장한다.
    StaticProfile은 props JSON blob에 병합해 저장한다.
    DynamicState에는 trait_* 컬럼이 없어 저장을 건너뛴다 (메모리 전용).
    """
    if not traits:
        return

    if source_label != "StaticProfile":
        # DynamicState 스키마에 trait_* 컬럼이 없으므로 저장 불가
        print(f"[TraitsInit] {char_id}: DynamicState에 trait 저장 불가 — 이번 턴 메모리 전용")
        return

    # StaticProfile.props는 JSON blob — 기존 데이터를 읽어 traits를 병합한 뒤 덮어쓴다
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(n:StaticProfile)
            RETURN n.props AS props_json
        """, cid=char_id)
        row = await rec.single()
        current: dict = {}
        if row and row["props_json"]:
            try:
                current = json.loads(row["props_json"])
            except (ValueError, TypeError):
                pass
        current.update(traits)
        await session.run(
            "MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(n:StaticProfile) SET n.props = $props_json",
            cid=char_id, props_json=json.dumps(current, ensure_ascii=False),
        )


def _default_traits() -> dict:
    """모든 trait 키를 0.0으로 초기화한 기본값 딕셔너리를 반환한다."""
    return {k: 0.0 for k in ALL_TRAIT_KEYS}


# ════════════════════════════════════════════════════════════
# 욕구 상수 (구 needs_manager.py)
# ════════════════════════════════════════════════════════════

THRESHOLD = 0.8

NEED_BASE_RATES: dict[str, float] = {
    "hunger": 0.0033,
    "rest":   0.0011,
    "social": 0.00035,
    "fun":    0.00069,
    "safety": 0.001,
    "libido": 0.00017,
}

NEED_DEFAULTS: dict[str, float] = {
    "hunger": 0.3,
    "rest":   0.2,
    "social": 0.1,
    "fun":    0.4,
    "safety": 0.05,
    "libido": 0.2,
}

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
            "libido_hints":   {npc_id: hint_str},
            "events_created": [event_id, ...]
        }
    """
    if elapsed_minutes <= 0:
        return {"libido_hints": {}, "events_created": []}

    npcs = await _fetch_all_npcs(exclude_id=pc_id)
    libido_hints:   dict[str, str] = {}
    events_created: list[str]      = []

    for npc in npcs:
        npc_id = npc["id"]

        traits, needs, profile = await asyncio.gather(
            ensure_traits(npc_id),
            _fetch_needs(npc_id),
            _fetch_profile_props(npc_id),
        )

        if profile.get("libido_excluded", False):
            continue

        updates: dict[str, float] = {}

        for need_name, base_rate in NEED_BASE_RATES.items():
            old_val = _as_float(needs.get(need_name), NEED_DEFAULTS[need_name])

            if need_name == "safety":
                new_val = await _apply_safety_decay(npc_id, old_val, elapsed_minutes)
                updates[need_name] = new_val
                continue

            multiplier = _calc_multiplier(need_name, traits, needs, profile)
            eff_rate   = base_rate * multiplier
            overflow_cnt, settled_val = _count_overflows(old_val, elapsed_minutes, eff_rate)

            if need_name == "libido":
                new_val = (
                    min(1.0, old_val + eff_rate * elapsed_minutes)
                    if overflow_cnt == 0
                    else min(1.0, settled_val + 0.1)
                )
                if overflow_cnt >= 1:
                    hint = _build_libido_hint(npc_id, profile, needs, traits)
                    if hint:
                        libido_hints[npc_id] = hint
                updates[need_name] = round(new_val, 4)
                continue

            if overflow_cnt == 0:
                updates[need_name] = round(min(1.0, old_val + eff_rate * elapsed_minutes), 4)

            elif overflow_cnt == 1 and need_name in AUTONOMOUS_NEEDS:
                overflow_time = current_time - timedelta(
                    minutes=(elapsed_minutes - (THRESHOLD - old_val) / eff_rate)
                )
                personality = profile.get("personality", "")
                result = await resolve_action(
                    npc_id, need_name, overflow_time,
                    needs.get("location_id", "unknown"),
                    personality, traits,
                )
                if result:
                    events_created.append(result["event_id"])
                time_after_resolve = (
                    elapsed_minutes
                    - (THRESHOLD - old_val) / eff_rate
                    - _as_float(result.get("duration_minutes") if result else None, 0.0)
                )
                settle  = SETTLE_LEVELS.get(need_name, 0.2)
                new_val = min(1.0, settle + eff_rate * max(0, time_after_resolve))
                updates[need_name] = round(new_val, 4)

            else:
                updates[need_name] = round(settled_val, 4)

        await _write_needs(npc_id, updates)

    return {"libido_hints": libido_hints, "events_created": events_created}


# ════════════════════════════════════════════════════════════
# 욕구 수치 계산
# ════════════════════════════════════════════════════════════

def _count_overflows(
    old_val:        float,
    elapsed_min:    float,
    effective_rate: float,
) -> tuple[int, float]:
    """
    elapsed_min 동안 욕구가 THRESHOLD를 몇 번 초과했는지 계산.
    반환: (초과 횟수, 마지막 정산 후 현재 수치 추정값)
    """
    if effective_rate <= 0:
        return 0, old_val

    time_to_first = (THRESHOLD - old_val) / effective_rate

    if elapsed_min < time_to_first:
        return 0, min(1.0, old_val + effective_rate * elapsed_min)

    remaining_after_first = elapsed_min - time_to_first
    cycle_time            = THRESHOLD / effective_rate
    additional_overflows  = int(remaining_after_first / cycle_time)
    overflows             = 1 + additional_overflows

    time_in_last_cycle = remaining_after_first - additional_overflows * cycle_time
    settle_base        = 0.2
    settled_val        = min(1.0, settle_base + effective_rate * time_in_last_cycle)

    return overflows, settled_val


def _as_float(value, default: float = 0.0) -> float:
    """Nullable DB values and malformed LLM values fall back to a numeric default."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    """Nullable DB values and malformed LLM values fall back to an integer default."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        m += _as_float(t.get("trait_gluttony"), 0.0) * 0.4
        return max(0.3, m)

    elif need == "rest":
        m = 1.0
        m += _as_float(t.get("trait_laziness"), 0.0) * 0.5
        m += _as_float(t.get("trait_vitality"), 0.0) * -0.35
        m += _as_float(t.get("trait_light_sleeper"), 0.0) * 0.3
        physical = needs.get("physical_condition", "healthy")
        if physical in ("injured", "ill", "hospitalized"):
            m *= 1.4
        return max(0.3, m)

    elif need == "social":
        m = 1.0
        m += _as_float(t.get("trait_extroversion"), 0.0) * 1.0
        m += _as_float(t.get("trait_attention_seeking"), 0.0) * 0.6
        m += _as_float(t.get("trait_independence"), 0.0) * -0.4
        return max(0.2, m)

    elif need == "fun":
        m = 1.0
        m += _as_float(t.get("trait_hedonism"), 0.0) * 0.7
        m += _as_float(t.get("trait_curiosity"), 0.0) * 0.4
        stress = _sanitize_stress_level(_as_int(needs.get("stress_level"), 0))
        if stress and stress >= 7:
            m *= 0.5
        return max(0.2, m)

    elif need == "safety":
        m = 1.0
        m += _as_float(t.get("trait_anxiety_prone"), 0.0) * 0.5
        mental = needs.get("mental_condition", "stable")
        if mental in ("stressed", "anxious"):
            m *= 1.3
        return max(0.5, m)

    elif need == "libido":
        m = 1.0
        m += _as_float(t.get("trait_libido_drive"), 0.0) * 1.0
        m += _as_float(t.get("trait_hedonism"), 0.0) * 0.4
        m += _as_float(t.get("trait_intimacy_drive"), 0.0) * 0.3
        cycle_day = _as_int(needs.get("cycle_day"), 0)
        if 12 <= cycle_day <= 16:
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
    npc_id:      str,
    old_safety:  float,
    elapsed_min: float,
) -> float:
    """
    미해소 Event의 safety_impact × decay_rate 합산으로 Safety 재계산.
    Safety = base(0.05) + Σ[ impact × max(0, 1 - decay_rate × elapsed) ]
    """
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event)
            WHERE e.safety_impact > 0 AND e.safety_resolved = false
            RETURN e.safety_impact     AS impact,
                   e.safety_decay_rate AS decay_rate
        """, cid=npc_id)
        rows = await rec.data()

    total = 0.05
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
    tendency    = profile.get("sexual_tendency", [])
    location_id = needs.get("location_id", "")
    partner_id  = profile.get("libido_partner", "")

    if "repressed" in tendency:
        return (
            f"[NEEDS_HINT:{npc_id}] Libido is suppressed — "
            "increases sensitivity and visible tension. Do NOT depict resolution."
        )

    if "villa" in location_id or "home" in location_id or "205" in location_id:
        privacy = "private"
    elif "bathroom" in location_id or "restroom" in location_id:
        privacy = "semi-private"
    else:
        privacy = "public"

    has_partner = bool(partner_id)

    if privacy == "private":
        hint = (
            "initiate_intimacy — body language: lingering gaze, casual touch, proximity"
            if has_partner
            else "solo_relief — brief withdrawal, sounds from another room"
        )
    elif privacy == "semi-private":
        if "exhibitionism" in tendency or "light_exhibitionism" in tendency:
            hint = "exhibitionism_urge — small daring gesture, checking if observed"
        else:
            hint = "seek_private_space — restless, distracted, excuses self"
    else:
        if "exhibitionism" in tendency:
            hint = "exhibitionism_urge — subtle but deliberate exposure gesture"
        elif has_partner:
            hint = "suppress + think_of_partner — distracted eye contact / brief touch"
        else:
            hint = "suppress — heightened sensory awareness, brief distraction"

    return (
        f"[NEEDS_HINT:{npc_id}] Libido 0.8+. "
        f"Behavior hint: {hint}. Do NOT narrate the need explicitly."
    )


# ════════════════════════════════════════════════════════════
# DB 읽기 / 쓰기
# ════════════════════════════════════════════════════════════

async def _fetch_all_npcs(exclude_id: str) -> list[dict]:
    """모든 NPC (PC 제외) 반환."""
    async with async_driver.session() as session:
        # libido_excluded는 StaticProfile 스키마에 정의되지 않아 Kuzu Binder 오류 발생
        # → 필터 없이 모든 NPC 반환 (PC 제외)
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.id <> $exclude
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
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_STATE]->(d:DynamicState)
            RETURN d AS props
        """, cid=npc_id)
        row = await rec.single()
        dynamic_props = dict(row["props"]) if row and row["props"] else {}

        rec2 = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN n AS props
        """, cid=npc_id)
        row2 = await rec2.single()
        needs_props = {f: v for f, v in NEED_DEFAULTS.items()}
        if row2 and row2["props"]:
            needs_props.update(dict(row2["props"]))
            needs_props.update(dynamic_props)
            return needs_props

        defaults       = dict(needs_props)
        defaults["id"] = f"{npc_id}_needs"
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_NEEDS]->(n:NeedsState {
                id:     $id,
                hunger: $hunger,
                rest:   $rest,
                social: $social,
                fun:    $fun,
                safety: $safety,
                libido: $libido
            })
        """, cid=npc_id, **defaults)
        defaults.update(dynamic_props)
        return defaults


async def _fetch_profile_props(npc_id: str) -> dict:
    """StaticProfile 속성 반환 (sexual_tendency, libido_* 등)."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props_json
        """, cid=npc_id)
        row = await rec.single()
        if not row or not row["props_json"]:
            return {}
        raw = row["props_json"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return {}
        return raw if isinstance(raw, dict) else {}


async def _write_needs(npc_id: str, updates: dict) -> None:
    """욕구 수치를 NeedsState에 저장."""
    if not updates:
        return

    need_keys    = set(NEED_BASE_RATES.keys())
    need_updates = {k: v for k, v in updates.items() if k in need_keys}
    if not need_updates:
        return

    set_clause = ", ".join(f"n.{k} = ${k}" for k in need_updates)
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_NEEDS]->(n:NeedsState)
            RETURN n.id AS nid
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            defaults = {f: need_updates.get(f, v) for f, v in NEED_DEFAULTS.items()}
            defaults["id"] = f"{npc_id}_needs"
            await session.run("""
                MATCH (c:Character {id: $cid})
                CREATE (c)-[:HAS_NEEDS]->(n:NeedsState {
                    id:     $id,
                    hunger: $hunger,
                    rest:   $rest,
                    social: $social,
                    fun:    $fun,
                    safety: $safety,
                    libido: $libido
                })
            """, cid=npc_id, **defaults)
            return

        await session.run(
            f"MATCH (c:Character {{id: $cid}})-[:HAS_NEEDS]->(n:NeedsState) SET {set_clause}",
            cid=npc_id, **need_updates,
        )
