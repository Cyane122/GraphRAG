"""
캐릭터 StaticProfile에 trait_* 필드가 없을 때
Haiku가 personality 문자열을 보고 생성 → DB에 영구 주입.

호출: needs_manager가 ensure_traits() 내에서 자동으로 호출.
재생성 없음 — 한 번 쓰면 끝.
"""

import os
from src.utils.db_utils import async_driver
from src.utils.llm_utils import async_llm_client, extract_json_from_llm

TRAITS_MODEL = os.getenv("MODEL_STATE_UPDATER", "claude-haiku-4-5-20251001")

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


def _is_traits_complete(props: dict) -> bool:
    return all(k in props for k in REQUIRED_KEYS)


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

    if _is_traits_complete(profile):
        return {k: profile[k] for k in ALL_TRAIT_KEYS if k in profile}

    personality = profile.get("personality", "")
    role        = profile.get("role", profile.get("job", ""))
    age         = profile.get("age", "")
    print(f"[TraitsInit] {char_id}: trait_* 없음 → Haiku 생성 중...")

    generated = await _generate_traits_from_personality(char_id, personality, role, str(age))
    await _write_traits_to_db(char_id, source_label, generated)
    print(f"[TraitsInit] {char_id}: 트레이트 생성 완료 → DB 저장")

    return generated


# ════════════════════════════════════════════════════════════
# Internal helpers
# ════════════════════════════════════════════════════════════

async def _load_profile(char_id: str) -> tuple[dict, str]:
    """StaticProfile → DynamicState 순으로 탐색. (props, label) 반환."""
    async with async_driver.session() as session:
        for rel, label in [("HAS_PROFILE", "StaticProfile"), ("HAS_STATE", "DynamicState")]:
            rec = await session.run(f"""
                MATCH (c:Character {{id: $cid}})-[:{rel}]->(n)
                RETURN properties(n) AS props
            """, cid=char_id)
            row = await rec.single()
            if row and row["props"]:
                return dict(row["props"]), label
    return {}, ""


async def _generate_traits_from_personality(
    char_id: str, personality: str, role: str, age: str
) -> dict:
    prompt = f"""You are a character trait analyzer for a Korean roleplay system.
Given a character's personality keywords, role, and age, return numeric trait values.

Character: {char_id}
Age: {age}
Role: {role}
Personality: {personality}

Return ONLY a JSON object with these exact keys and float values between -1.0 and 1.0.
Positive = strong presence of the trait. Negative = opposite tendency. 0 = neutral.

Keys:
trait_laziness, trait_vitality, trait_gluttony, trait_light_sleeper,
trait_sensitivity, trait_extroversion, trait_introversion,
trait_attention_seeking, trait_independence, trait_empathy, trait_jealousy,
trait_ambition, trait_perfectionism, trait_curiosity, trait_control_need,
trait_achievement_drive, trait_anxiety_prone,
trait_hedonism, trait_impulsivity, trait_adventurousness,
trait_comfort_seeking, trait_indulgence, trait_risk_aversion,
trait_attachment, trait_possessiveness, trait_trust, trait_loyalty,
trait_intimacy_drive, trait_dependency,
trait_responsibility, trait_self_esteem, trait_morality,
trait_pride, trait_diligence, trait_stubbornness, trait_libido_drive

Examples of mapping:
- "calm+logical+aloof"     → extroversion: -0.4, impulsivity: -0.6, control_need: 0.5
- "loud+energetic+social"  → extroversion: 0.9, vitality: 0.8, laziness: -0.5
- "strict+perfectionist"   → diligence: 0.9, perfectionism: 0.9, control_need: 0.8

NO explanation. ONLY JSON."""

    try:
        resp = await async_llm_client.messages.create(
            model=TRAITS_MODEL,
            max_tokens=512,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        raw    = resp.content[0].text
        parsed = extract_json_from_llm(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not a dict")
        return {k: max(-1.0, min(1.0, float(v))) for k, v in parsed.items() if k in ALL_TRAIT_KEYS}
    except Exception as e:
        print(f"[TraitsInit] Haiku 생성 실패 ({char_id}): {e} → 기본값")
        return _default_traits()


async def _write_traits_to_db(char_id: str, source_label: str, traits: dict) -> None:
    if not traits:
        return
    set_clause = ", ".join(f"n.{k} = ${k}" for k in traits)
    rel = "HAS_PROFILE" if source_label == "StaticProfile" else "HAS_STATE"
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (c:Character {{id: $cid}})-[:{rel}]->(n) SET {set_clause}",
            cid=char_id, **traits,
        )


def _default_traits() -> dict:
    return {k: 0.0 for k in ALL_TRAIT_KEYS}