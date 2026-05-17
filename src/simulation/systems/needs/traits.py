# ================================
# src/simulation/systems/needs/traits.py
#
# Fill missing trait values from NPC StaticProfile and DynamicState.
#
# Functions
#   - ensure_traits(char_id: str) -> dict : Generate and save missing trait_* fields with the LLM
#   - ensure_traits_for_characters(characters: list[dict]) -> dict : Initialize traits from a character list
#   - _default_traits() -> dict : Return all trait keys set to 0.0
#   - _as_float(value, default: float = 0.0) -> float : Safe float conversion
# ================================
import json

from src.config import MODEL_STATE_UPDATER as TRAITS_MODEL
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm

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


async def ensure_traits_for_characters(characters: list[dict]) -> dict:
    """등장인물 목록을 기준으로 누락된 trait 값을 초기화합니다."""
    initialized: dict[str, dict] = {}
    skipped = 0
    failed: list[str] = []

    for character in characters:
        char_id = str(character.get("id") or "").strip()
        if not char_id:
            skipped += 1
            continue
        try:
            initialized[char_id] = await ensure_traits(char_id)
        except Exception as e:
            failed.append(char_id)
            print(f"[TraitsInit] {char_id}: 등장인물 목록 기반 초기화 실패: {e}")

    return {
        "total": len(characters),
        "initialized": initialized,
        "skipped": skipped,
        "failed": failed,
    }


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


def _as_float(value, default: float = 0.0) -> float:
    """값을 안전하게 float로 변환합니다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

