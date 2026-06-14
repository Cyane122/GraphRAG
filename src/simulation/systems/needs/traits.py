# ================================
# src/simulation/systems/needs/traits.py
#
# Fill missing bipolar trait-axis values from NPC StaticProfile and DynamicState.
#
# Functions
#   - ensure_traits(char_id: str) -> dict : Generate and save missing bipolar trait_* fields with the LLM
#   - ensure_traits_for_characters(characters: list[dict]) -> dict : Initialize traits from a character list
#   - _has_nonzero_traits(props: dict) -> bool : Return whether saved traits contain a real bias
#   - _trait_cache_context() -> tuple[str, str] : Return active world/scenario cache context
#   - _safe_cache_name(value: str | None) -> str : Convert a context value into a safe path segment
#   - _trait_cache_path() -> Path : Return the per-world/per-scenario local trait cache path
#   - _legacy_trait_cache_path() -> Path | None : Return the legacy per-world cache path
#   - _read_trait_cache() -> dict : Read local generated trait cache
#   - _write_trait_cache(cache: dict) -> None : Persist local generated trait cache
#   - _get_cached_traits(char_id: str) -> dict : Return cached traits for a character
#   - _set_cached_traits(char_id: str, traits: dict) -> None : Store generated traits for a character
#   - _sanitize_traits(values: dict) -> dict : Clamp and complete trait values
#   - _default_traits() -> dict : Return all trait keys set to 0.0
#   - _as_float(value: object, default: float = 0.0) -> float : Safe float conversion
# ================================
import asyncio
import json
import re
from pathlib import Path

from src.config import MODEL_STATE_UPDATER as TRAITS_MODEL, WORLD_ID
from src.core.database import async_driver
from src.core.llm.client import get_model, extract_json_from_llm

ALL_TRAIT_KEYS = [
    "trait_direction_of_energy",
    "trait_recognition",
    "trait_judgement",
    "trait_life_pattern",
    "trait_achievement_orientation",
    "trait_emotional_reactivity",
    "trait_attachment_orientation",
    "trait_social_attention",
    "trait_control_orientation",
    "trait_moral_orientation",
    "trait_pleasure_orientation",
    "trait_trust_orientation",
    "trait_vitality",
    "trait_self_esteem",
    "trait_empathy",
    "trait_relational_exclusivity",
]

REQUIRED_KEYS = ALL_TRAIT_KEYS

_CACHE_VERSION = 2
_DEFAULT_SCENARIO_ID = "default"

TRAIT_AXIS_DESCRIPTIONS = {
    "trait_direction_of_energy": "Direction of Energy: extroverted(+), introverted(-). Where the character gains energy.",
    "trait_recognition": "Recognition: sensing(+), intuitive(-). How the character processes information.",
    "trait_judgement": "Judgement: rational(+), emotional(-). What the character bases decisions on.",
    "trait_life_pattern": "Life Pattern: planned(+), spontaneous(-). How the character structures life.",
    "trait_achievement_orientation": "Achievement Orientation: achievement-oriented(+), stability-oriented(-). How the character approaches challenge.",
    "trait_emotional_reactivity": "Emotional Reactivity: sensitive(+), insensitive(-). How well the character notices emotional shifts around them.",
    "trait_attachment_orientation": "Attachment Orientation: dependent(+), independent(-). How the character forms attachment.",
    "trait_social_attention": "Social Attention: attention-seeking(+), low-exposure(-). Whether the character likes being noticed.",
    "trait_control_orientation": "Control Orientation: leading(+), compliant(-). How the character handles situations.",
    "trait_moral_orientation": "Moral Orientation: principle-centered(+), pragmatic compromise(-). How the character applies moral principles.",
    "trait_pleasure_orientation": "Pleasure Orientation: pleasure-seeking(+), restrained(-). How openly the character follows desire.",
    "trait_trust_orientation": "Trust Orientation: trusting(+), guarded(-). Whether the character trusts others easily.",
    "trait_vitality": "Vitality: energetic(+), low-vitality(-). Whether the character's activity level is high.",
    "trait_self_esteem": "Self-Esteem: high self-esteem(+), low self-esteem(-). How strongly the character values themself.",
    "trait_empathy": "Empathy: empathic(+), cold(-). How much the character empathizes with others' emotions.",
    "trait_relational_exclusivity": "Relational Exclusivity: possessive(+), open(-). How the character views others' relationships.",
}


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
    StaticProfile (또는 DynamicState)에 16개 trait 축이 없으면
    LLM으로 생성해 DB에 저장 후 반환.
    이미 존재하면 DB 조회 결과 그대로 반환 (LLM 호출 없음).
    """
    profile, source_label = await _load_profile(char_id)
    if not profile:
        print(f"[TraitsInit] {char_id}: 프로필 없음 → 기본값 반환")
        return _default_traits()

    if _is_traits_complete(profile) and _has_nonzero_traits(profile):
        traits = _sanitize_traits(profile)
        if not await _get_cached_traits(char_id):
            await _set_cached_traits(char_id, traits)
        return traits

    cached = await _get_cached_traits(char_id)
    if cached:
        await _write_traits_to_db(char_id, source_label, cached)
        print(f"[TraitsInit] {char_id}: trait_* 없음 → 로컬 캐시 사용")
        return cached

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

    print(f"[TraitsInit] {char_id}: 새 trait 축 없음 → LLM 생성 중...")

    generated = await _generate_traits_from_personality(char_id, personality, role, str(age))
    if not generated:
        print(f"[TraitsInit] {char_id}: 트레이트 생성 실패 → DB 저장 생략")
        return _default_traits()

    await _write_traits_to_db(char_id, source_label, generated)
    await _set_cached_traits(char_id, generated)
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
    """LLM에 personality 문자열을 주고 16개 양극 trait 축 점수 JSON을 생성한다."""
    keys_inline = "\n".join(
        f'- "{key}": {description}'
        for key, description in TRAIT_AXIS_DESCRIPTIONS.items()
    )

    system_instruction = (
        "You are a character trait analyzer. "
        "You output ONLY raw JSON — no markdown, no code fences, no explanation. "
        "Your response must start with { and end with }."
    )

    prompt = f"""Generate bipolar trait-axis scores for {char_id} (age={age}, role={role}):
{personality}

Output: a single JSON object with exactly ALL {len(ALL_TRAIT_KEYS)} keys below.
Each value must be a float from -1.0 to +1.0.
Positive and negative are not superior/inferior; they only represent opposite concepts.
Example: trait_direction_of_energy=-0.7 means introverted 0.7.

Keys and meanings:
{keys_inline}

Examples:
- "calm/logical/aloof" -> trait_judgement:0.7, trait_direction_of_energy:-0.5, trait_life_pattern:0.2
- "loud/energetic/social" -> trait_direction_of_energy:0.9, trait_social_attention:0.5, trait_vitality:0.8
- "strict/perfectionist/principled" -> trait_life_pattern:0.8, trait_moral_orientation:0.7, trait_control_orientation:0.4

Return ONLY raw JSON."""

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

        valid_values = {k: v for k, v in parsed.items() if k in ALL_TRAIT_KEYS}
        if not valid_values:
            print(f"[TraitsInit] {char_id}: 유효 trait 키 없음 → 저장 생략")
            return {}

        missing = [k for k in ALL_TRAIT_KEYS if k not in valid_values]
        if missing:
            print(f"[TraitsInit] {char_id}: 누락 키 {len(missing)}개 → 0.0으로 채움")
        result = _sanitize_traits(valid_values)

        if not all(k in result for k in REQUIRED_KEYS):
            print(f"[TraitsInit] {char_id}: 필수 키 누락, 생성 실패로 간주 → 저장 생략")
            return {}

        return result

    except Exception as e:
        print(f"[TraitsInit] LLM 생성 실패 ({char_id}): {e} → 저장 생략")
        return {}


async def _write_traits_to_db(char_id: str, source_label: str, traits: dict) -> None:
    """trait 딕셔너리를 DB에 저장한다.
    StaticProfile은 props JSON blob에 병합해 저장한다. StaticProfile이 없고
    DynamicState fallback으로 생성된 값이면 최소 StaticProfile을 만들어 저장한다.
    """
    if not traits:
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
        current = {key: value for key, value in current.items() if not key.startswith("trait_")}
        current.update(traits)
        props_json = json.dumps(current, ensure_ascii=False)
        if row:
            await session.run(
                "MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(n:StaticProfile) SET n.props = $props_json",
                cid=char_id, props_json=props_json,
            )
            return

        await session.run("""
            MATCH (c:Character {id: $cid})
            WHERE NOT (c)-[:HAS_PROFILE]->()
            CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                id: $profile_id,
                props: $props_json,
                age: 0,
                gender: "",
                role: ""
            })
        """, cid=char_id, profile_id=f"{char_id}_static", props_json=props_json)
        if source_label != "StaticProfile":
            print(f"[TraitsInit] {char_id}: StaticProfile 생성 후 trait 저장")


def _trait_cache_context() -> tuple[str, str]:
    """trait 캐시 컨텍스트(world/scenario)를 반환합니다. web UI에서는 기본 world/scenario를 사용한다."""
    return _safe_cache_name(WORLD_ID), _safe_cache_name(_DEFAULT_SCENARIO_ID)


def _safe_cache_name(value: str | None) -> str:
    """캐시 파일 경로에 안전한 이름으로 변환합니다."""
    text = str(value or _DEFAULT_SCENARIO_ID).strip() or _DEFAULT_SCENARIO_ID
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def _trait_cache_path() -> Path:
    """현재 world/scenario에 해당하는 로컬 trait 캐시 파일 경로를 반환합니다."""
    world_id, scenario_id = _trait_cache_context()
    return Path("data") / "trait_cache" / world_id / f"{scenario_id}.json"


def _legacy_trait_cache_path() -> Path | None:
    """이전 WORLD_ID 단일 파일 캐시 경로를 반환합니다."""
    world_id, scenario_id = _trait_cache_context()
    if scenario_id != _DEFAULT_SCENARIO_ID:
        return None
    return Path("data") / "trait_cache" / f"{world_id}.json"


async def _read_trait_cache() -> dict:
    """로컬 trait 캐시를 UTF-8 JSON으로 읽고 없거나 깨졌으면 빈 dict를 반환합니다."""
    path = _trait_cache_path()
    legacy_path = _legacy_trait_cache_path()

    def _read() -> dict:
        """동기 파일 읽기를 작은 작업 단위로 감쌉니다."""
        read_path = path if path.exists() else legacy_path
        if read_path is None or not read_path.exists():
            return {}
        try:
            with read_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    return await asyncio.to_thread(_read)


async def _write_trait_cache(cache: dict) -> None:
    """로컬 trait 캐시를 UTF-8 JSON으로 저장합니다."""
    path = _trait_cache_path()

    def _write() -> None:
        """동기 파일 쓰기를 작은 작업 단위로 감쌉니다."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)

    await asyncio.to_thread(_write)


async def _get_cached_traits(char_id: str) -> dict:
    """캐시에 저장된 캐릭터 trait을 정규화해 반환합니다."""
    cache = await _read_trait_cache()
    if cache.get("version") != _CACHE_VERSION:
        return {}

    world_traits = cache.get("traits")
    if not isinstance(world_traits, dict):
        return {}

    raw_traits = world_traits.get(char_id)
    if not isinstance(raw_traits, dict):
        return {}

    traits = _sanitize_traits(raw_traits)
    return traits if _is_traits_complete(traits) else {}


async def _set_cached_traits(char_id: str, traits: dict) -> None:
    """생성된 캐릭터 trait을 로컬 캐시에 저장합니다."""
    clean_traits = _sanitize_traits(traits)
    if not _is_traits_complete(clean_traits):
        return

    cache = await _read_trait_cache()
    if cache.get("version") != _CACHE_VERSION:
        world_id, scenario_id = _trait_cache_context()
        cache = {
            "version": _CACHE_VERSION,
            "world_id": world_id,
            "scenario_id": scenario_id,
            "traits": {},
        }
    cache.setdefault("traits", {})[char_id] = clean_traits
    await _write_trait_cache(cache)


def _sanitize_traits(values: dict) -> dict:
    """trait 값을 -1.0~1.0 범위로 보정하고 누락 키를 0.0으로 채웁니다."""
    result: dict[str, float] = {}
    for key in ALL_TRAIT_KEYS:
        value = values.get(key, 0.0)
        result[key] = max(-1.0, min(1.0, _as_float(value, 0.0)))
    return result


def _default_traits() -> dict:
    """모든 trait 키를 0.0으로 초기화한 기본값 딕셔너리를 반환한다."""
    return {k: 0.0 for k in ALL_TRAIT_KEYS}


def _as_float(value: object, default: float = 0.0) -> float:
    """값을 안전하게 float로 변환합니다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
