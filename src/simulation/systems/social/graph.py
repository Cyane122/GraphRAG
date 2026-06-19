# ================================
# src/simulation/systems/social/graph.py
#
# Character identity resolution, transient NPC creation, and event linking for the Social system.
#
# Functions
#   - _cache_key() -> str : Return the current session's cache key (db_path or '__global__')
#   - _get_known_chars() -> dict[str, str] : Fetch character names and aliases (per-session cached)
#   - _get_primary_names() -> dict[str, str] : Fetch character id -> primary name map
#   - _invalidate_cache() -> None : Invalidate the current session's character cache
#   - _resolve_identity(name: str, known: dict[str, str]) -> str | None : Resolve a name to char_id
#   - _create_stub(name_kor: str, main_npc_id: str, pc_id: str, world_config: dict, allow_existing_alias_match: bool = True, source_text: str = "") -> str | None : Create a conservative transient NPC
#   - _is_stub_candidate(name_kor: str) -> bool : Return whether text is concrete enough to create a stub
#   - _normalize_relation_descriptor_for_family(name_kor: str) -> str | None : Validate sibling descriptors against StaticProfile.family
#   - _build_conservative_stub_profile(name_kor: str, main_npc_id: str, source_text: str = "", world_config: dict | None = None) -> dict : Build a transient stub from evidence and plausible defaults
#   - _increment_appearance(char_id: str) -> None : Increment appearance_count
#   - _link_to_event(char_id: str, event_id: str) -> None : Link a character to an event
#   - _romanize_hangul(text: str) -> str : Transliterate Hangul syllables to Roman (Revised Romanization approx.)
#   - _kor_to_roman_id(name_kor: str) -> str : Build a name-shaped char_id from a Korean name (e.g. '민지' → 'minji')
# ================================
import hashlib
import json
import re
from datetime import datetime

from src.config import MODEL_STATE_UPDATER as STUB_MODEL
from src.core.database import async_driver
from src.core.database.helpers import ensure_relationship, update_dynamic_state
from src.simulation.systems.social.models import StubProfile

# db_path → {name/alias/id: char_id}. 세션별로 격리해 멀티세션 오염을 방지한다.
_known_chars_cache: dict[str, dict[str, str]] = {}
_KINSHIP_DESCRIPTOR_RE = re.compile(
    "|".join(
        [
            "\ub0a8\ub3d9\uc0dd",
            "\uc5ec\ub3d9\uc0dd",
            "\ub3d9\uc0dd",
            "\uc624\ube60",
            "\uc5b8\ub2c8",
            "\ub204\ub098",
            "\ud615",
            "\uc544\ube60",
            "\uc5c4\ub9c8",
            "\uc544\ubc84\uc9c0",
            "\uc5b4\uba38\ub2c8",
            "\uce5c\uad6c",
            "\uc120\ubc30",
            "\ud6c4\ubc30",
        ]
    )
)
_SAME_SURNAME_ROLE_SET: frozenset[str] = frozenset({
    "\ub0a8\ub3d9\uc0dd",
    "\uc5ec\ub3d9\uc0dd",
    "\ub3d9\uc0dd",
    "\ud615",
    "\uc624\ube60",
    "\uc5b8\ub2c8",
    "\ub204\ub098",
    "\uc544\ubc84\uc9c0",
    "\uc544\ube60",
    "\ud560\uc544\ubc84\uc9c0",
})
# family 로 태그할 호칭. 성 공유 여부와 무관하다 — 엄마/어머니는 다른 성이지만 가족이다.
_FAMILY_ROLE_SET: frozenset[str] = _SAME_SURNAME_ROLE_SET | frozenset({
    "엄마", "어머니", "할머니",
})

_FALLBACK_GIVEN_NAMES: tuple[str, ...] = (
    "\ub3c4\uc724",
    "\uc11c\uc900",
    "\ubbfc\uc900",
    "\uc9c0\ud638",
    "\ud558\uc900",
    "\uc720\ucc2c",
    "\uc740\uc6b0",
    "\uc2dc\uc6b0",
)
_KOREAN_NAME_RE = re.compile(r"^[\uac00-\ud7a3]{2,4}$")
_KOREAN_SURNAME_ROMAN: dict[str, str] = {
    "\uae40": "kim", "\uc774": "lee", "\ubc15": "park", "\ucd5c": "choi", "\uc815": "jung",
    "\uac15": "kang", "\uc870": "jo", "\uc724": "yoon", "\uc7a5": "jang", "\uc784": "lim",
    "\ud55c": "han", "\uc624": "oh", "\uc11c": "seo", "\uc2e0": "shin", "\uad8c": "kwon",
    "\ud669": "hwang", "\uc548": "ahn", "\uc1a1": "song", "\ub958": "ryu", "\uc804": "jun",
    "\ud64d": "hong", "\uace0": "ko", "\ubb38": "moon", "\uc591": "yang", "\uc190": "son",
    "\ubc30": "bae", "\ubc31": "baek", "\ud5c8": "heo", "\uc720": "yoo", "\ub0a8": "nam",
    "\uc2ec": "shim", "\ub178": "noh", "\ud558": "ha", "\uc9c4": "jin", "\uc5c4": "eom",
    "\ubcc0": "byun", "\uc6b0": "woo", "\uad6c": "koo", "\ubbfc": "min", "\ub098": "na",
}
_FALLBACK_GIVEN_NAMES_ROMAN: tuple[str, ...] = (
    "doyun", "seojun", "minjun", "jiho", "hajun", "yuchan", "eunwoo", "siwoo",
)
_SIBLING_ROLE_SET: frozenset[str] = frozenset({
    "\ub3d9\uc0dd",
    "\ub0a8\ub3d9\uc0dd",
    "\uc5ec\ub3d9\uc0dd",
})
_BROTHER_MARKERS: tuple[str, ...] = (
    "younger brother",
    "little brother",
    "brother",
    "\ub0a8\ub3d9\uc0dd",
)
_SISTER_MARKERS: tuple[str, ...] = (
    "younger sister",
    "little sister",
    "sister",
    "\uc5ec\ub3d9\uc0dd",
)
_NO_SIBLING_MARKERS: tuple[str, ...] = (
    "no siblings",
    "only child",
    "\uc678\ub3d9",
    "\ud615\uc81c\uac00 \uc5c6",
    "\uc790\ub9e4\uac00 \uc5c6",
)
_APPEARANCE_MARKERS: tuple[str, ...] = (
    "키", "몸", "체형", "얼굴", "머리", "머리카락", "눈", "입술", "피부", "외모",
    "인상", "표정", "복장", "옷", "유니폼", "제복", "앞치마", "화장", "향수",
    "마른", "통통", "작은", "큰", "긴", "짧은", "검은", "갈색", "금발",
)
_ROLE_MARKERS: tuple[str, ...] = (
    "직원", "종업원", "알바", "사장", "손님", "친구", "선배", "후배", "동급생",
    "동료", "가족", "남동생", "여동생", "동생", "언니", "누나", "오빠", "형",
    "아버지", "어머니", "엄마", "아빠", "담당", "관리자", "경호원", "운전기사",
)

def _cache_key() -> str:
    """캐릭터 이름 캐시 키 = 현재 활성 Kuzu DB 경로(스레드/대화별 격리). 활성 드라이버 없으면 '__global__'."""
    from src.core.database.driver import current_db_path

    return current_db_path() or "__global__"


async def _get_known_chars() -> dict[str, str]:
    """현재 세션의 캐릭터 이름→id 캐시를 반환한다. 최초 호출 시 DB 조회."""
    key = _cache_key()
    if key in _known_chars_cache:
        return _known_chars_cache[key]

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name, c.aliases AS aliases
        """)
        rows = await rec.data()

    result: dict[str, str] = {}
    for r in rows:
        if r["id"]:
            result[r["id"]] = r["id"]
        if r["name"]:
            result[r["name"]] = r["id"]
        for alias in (r["aliases"] or []):
            result[alias] = r["id"]

    _known_chars_cache[key] = result
    return result


async def _get_primary_names() -> dict[str, str]:
    """캐릭터 id -> 대표 이름 맵을 반환한다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name
        """)
        rows = await rec.data()
    return {
        str(row["id"]): str(row["name"])
        for row in rows
        if row.get("id") and row.get("name")
    }


def _invalidate_cache() -> None:
    """현재 세션의 캐릭터 캐시를 무효화한다."""
    _known_chars_cache.pop(_cache_key(), None)


def _resolve_identity(name: str, known: dict[str, str]) -> str | None:
    """이름이 known dict에 정확히 있으면 char_id를 반환한다."""
    if name in known:
        return known[name]
    if _KINSHIP_DESCRIPTOR_RE.search(name):
        return None
    return None


# 한글 음절 → 로마자(개정 로마자 표기 근사치). char_id 가독성용 — 음절 블록을
# 초성/중성/종성으로 분해해 매핑한다. 충돌은 _unique_char_id 가 처리한다.
_HANGUL_BASE = 0xAC00
_HANGUL_INITIALS = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp",
    "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]
_HANGUL_MEDIALS = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]
_HANGUL_FINALS = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l",
    "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
]


def _romanize_hangul(text: str) -> str:
    """한글 음절을 개정 로마자 근사치로 변환. 영숫자는 소문자로 유지, 그 외는 버린다."""
    out: list[str] = []
    for ch in text:
        code = ord(ch) - _HANGUL_BASE
        if 0 <= code < 11172:
            out.append(_HANGUL_INITIALS[code // 588])
            out.append(_HANGUL_MEDIALS[(code % 588) // 28])
            out.append(_HANGUL_FINALS[code % 28])
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


def _kor_to_roman_id(name_kor: str) -> str:
    """한국어 이름을 사람 이름 형태의 char_id 로 변환한다(예: '민지' → 'minji').

    난수/타임스탬프 없이 이름 자체를 쓰고, 중복은 _unique_char_id 가 _2, _3 … 으로
    해소한다. 한글이 전혀 없으면 마지막 안전장치로 'npc' 를 쓴다.
    """
    return _romanize_hangul(name_kor) or "npc"


async def _unique_char_id(base_id: str) -> str:
    """Character id 충돌을 피한 신규 id를 반환한다."""
    async with async_driver.session() as session:
        for idx in range(100):
            candidate = base_id if idx == 0 else f"{base_id}_{idx + 1}"
            rec = await session.run(
                "MATCH (c:Character {id: $cid}) RETURN c.id AS id",
                cid=candidate,
            )
            if await rec.single() is None:
                return candidate
    return f"{base_id}_{datetime.now().strftime('%H%M%S%f')}"


async def _create_stub(
    name_kor:    str,
    main_npc_id: str,
    pc_id:       str,
    world_config: dict,
    allow_existing_alias_match: bool = True,
    source_text: str = "",
) -> str | None:
    """Transient NPC stub 생성. char_id 반환."""
    original_name_kor = name_kor
    normalized_name = await _normalize_relation_descriptor_for_family(name_kor)
    if not normalized_name:
        return None
    name_kor = normalized_name
    if not _is_stub_candidate(name_kor):
        print(f"[WorldBuilder] transient stub rejected: {name_kor}")
        return None

    # 이름은 중복 생성을 막되, 별칭-only 매칭은 호출자가 허용한 경우에만 기존 인물로 흡수한다.
    alias_clause = "OR $name IN c.aliases OR $original_name IN c.aliases" if allow_existing_alias_match else ""
    async with async_driver.session() as session:
        rec = await session.run(f"""
            MATCH (c:Character)
            WHERE c.name = $name OR c.name = $original_name
               {alias_clause}
            RETURN c.id AS id
        """, name=name_kor, original_name=original_name_kor)
        row = await rec.single()
        if row:
            return row["id"]

    stub = await _build_conservative_stub_profile(name_kor, main_npc_id, source_text, world_config)

    # 관계 서술어는 고유 이름만 생성하고, 인물 설정은 명시된 정보만 보존한다.
    generated = (stub.get("name_kor") or "").strip()
    if _is_usable_generated_name(generated, name_kor):
        display_name = generated
    else:
        fallback_stub = await _build_conservative_stub_profile(name_kor, main_npc_id, source_text, world_config)
        display_name = fallback_stub["name_kor"]
        # display_name이 fallback이면 name_roman도 fallback 것을 유지해 id-name 불일치를 방지
        stub = {**fallback_stub, **{k: v for k, v in stub.items() if v and k not in ("name_kor", "name_roman")}}
    # 서술어('유람의 남동생')와 실제 이름이 다를 때 서술어를 aliases에 보관 (다음 턴 재인식용)
    aliases = []
    for alias in (original_name_kor, name_kor):
        if alias and alias != display_name and alias not in aliases:
            aliases.append(alias)

    # LLM 제공 name_roman 우선, 없으면 hash fallback
    raw_roman = (stub.get("name_roman") or "").strip().lower()
    raw_roman = re.sub(r'[^a-z0-9_]', '', raw_roman)
    base_id = raw_roman if raw_roman else _kor_to_roman_id(display_name)
    char_id = await _unique_char_id(base_id)
    timestamp = datetime.now().isoformat()

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: "transient"})
        """, id=char_id, name=display_name, aliases=aliases)

        # StaticProfile
        profile_json = json.dumps({
            "name_kor":              display_name,
            "type":                  "transient",
            "context":               stub.get("context", ""),
            "role":                  stub.get("relation_type", "acquaintance"),
            "biological_sex":        stub.get("biological_sex", ""),
            "age":                   stub.get("age", ""),
            "family":                stub.get("family", ""),
            "formative_background":  stub.get("formative_background", ""),
            "first_seen":            timestamp,
            "last_seen":             timestamp,
            "appearance_count":      0,
            "libido_excluded":       True,
        }, ensure_ascii=False)
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {id: $pid, props: $props_json})
        """,
            cid        = char_id,
            pid        = f"{char_id}_static",
            props_json = profile_json,
        )

        # Personality
        personality_json = json.dumps({
            "core_traits": stub.get("personality", "unknown"),
            "speech_style": stub.get("speech_style", ""),
        }, ensure_ascii=False)
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PERSONALITY]->(:Personality {id: $pid, props: $props_json})
        """,
            cid        = char_id,
            pid        = f"{char_id}_personality",
            props_json = personality_json,
        )

        # DynamicInformation — stub에서 얻은 물리 정보 취합 (helpers.DYNAMIC_INFORMATION_FIELDS 화이트리스트 내)
        info_props = json.dumps({
            "age":            stub.get("age", ""),
            "height":         stub.get("height", ""),
            "weight":         stub.get("weight", ""),
            "measurements":   stub.get("measurements", ""),
            "biological_sex": stub.get("biological_sex", ""),
            "appearance":     stub.get("appearance", ""),
            "summary":        stub.get("context") or "No durable dynamic information recorded yet.",
        }, ensure_ascii=False)
        await session.run("""
            MATCH (c:Character {id: $cid})
            WHERE NOT (c)-[:HAS_INFO]->()
            CREATE (c)-[:HAS_INFO]->(:DynamicInformation {id: $info_id, props: $props})
        """, cid=char_id, info_id=f"{char_id}_info", props=info_props)

        # DynamicState — stub initial_mood 반영
        initial_mood = stub.get("initial_mood") or "calm"
        await session.run("""
            MATCH (c:Character {id: $cid})
            WHERE NOT (c)-[:HAS_STATE]->()
            CREATE (c)-[:HAS_STATE]->(:DynamicState {
                id: $state_id,
                physical_condition: "healthy",
                mental_condition:   "stable",
                stress_level:       0,
                mood:               $mood,
                cycle_day:          1,
                location_id:        "",
                workplace_stress_level: 0,
                outfit:             "",
                injury_marks:       "",
                has_menstrual_cycle: false,
                pregnant:           false,
                pregnancy_day:      0,
                cum_shots_this_cycle: 0,
                emotional_state:    ""
            })
        """, cid=char_id, state_id=f"{char_id}_state", mood=initial_mood)

        # safety net: 이미 생성된 노드는 WHERE NOT 조건으로 건드리지 않음
        await _ensure_runtime_nodes_in_session(session, char_id)

    # 인라인 CREATE 가 다루지 않는 파생 DynamicState 컬럼을 초기화한다.
    # update_dynamic_state 가 스키마 타입에 맞춰 정규화(age→INT)하고 비정상 값은 버린다.
    await update_dynamic_state(char_id, {
        "physique": stub.get("physique", ""),
        "age":      stub.get("age", ""),
    })

    print(f"[WorldBuilder] Transient 생성: {name_kor} → {display_name} ({char_id})")
    await ensure_relationship(
        main_npc_id,
        char_id,
        rel_type=stub.get("relation_type", "acquaintance"),
        affinity=int(stub.get("initial_affinity") or 0),
        trust=10,
        current_status=stub.get("relation_status", "first encounter"),
    )
    return char_id


async def _ensure_runtime_nodes_in_session(session, char_id: str) -> None:
    """Attach DynamicState and DynamicInformation nodes if a character lacks them."""
    await session.run(
        """
        MATCH (c:Character {id: $cid})
        WHERE NOT (c)-[:HAS_STATE]->()
        CREATE (c)-[:HAS_STATE]->(:DynamicState {
            id: $state_id,
            physical_condition: "healthy",
            mental_condition: "stable",
            stress_level: 0,
            mood: "calm",
            cycle_day: 1,
            location_id: "",
            workplace_stress_level: 0,
            outfit: "",
            injury_marks: "",
            has_menstrual_cycle: false,
            pregnant: false,
            pregnancy_day: 0,
            cum_shots_this_cycle: 0,
            emotional_state: ""
        })
        """,
        cid=char_id,
        state_id=f"{char_id}_state",
    )
    await session.run(
        """
        MATCH (c:Character {id: $cid})
        WHERE NOT (c)-[:HAS_INFO]->()
        CREATE (c)-[:HAS_INFO]->(:DynamicInformation {id: $info_id, props: $props})
        """,
        cid=char_id,
        info_id=f"{char_id}_info",
        props=json.dumps({"summary": "No durable dynamic information recorded yet."}, ensure_ascii=False),
    )


async def ensure_character_runtime_nodes(char_id: str) -> None:
    """Ensure any PC or NPC has DynamicState and DynamicInformation nodes."""
    if not char_id:
        return
    async with async_driver.session() as session:
        await _ensure_runtime_nodes_in_session(session, char_id)


def _unique_ordered(values: list[str]) -> list[str]:
    """Return non-empty ids in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _initial_relationship_for_pair(
    source_id: str,
    target_id: str,
    source_type: str = "",
    target_type: str = "",
) -> dict:
    """Pick a conservative first-encounter relationship seed."""
    if source_id == target_id:
        return {}
    if "player" in {source_id, target_id}:
        return {
            "type": "acquaintance",
            "affinity": 0,
            "trust": 5,
            "current_status": "newly aware of each other",
        }
    if "transient" in {source_type, target_type}:
        return {
            "type": "acquaintance",
            "affinity": 0,
            "trust": 10,
            "current_status": "first encounter",
        }
    return {
        "type": "acquaintance",
        "affinity": 5,
        "trust": 15,
        "current_status": "lightly established acquaintance",
    }


async def _fetch_character_types(char_ids: list[str]) -> dict[str, str]:
    """Fetch Character.type values for relationship seeding."""
    result: dict[str, str] = {}
    async with async_driver.session() as session:
        for char_id in char_ids:
            rec = await session.run(
                "MATCH (c:Character {id: $cid}) RETURN c.type AS type",
                cid=char_id,
            )
            row = await rec.single()
            result[char_id] = str(row["type"] or "") if row else ""
    return result


async def ensure_scene_relationships(participant_ids: list[str]) -> None:
    """Ensure directed relationships exist between every character in a scene."""
    participants = _unique_ordered(participant_ids)
    if len(participants) < 2:
        return

    for char_id in participants:
        await ensure_character_runtime_nodes(char_id)

    char_types = await _fetch_character_types(participants)
    for source_id in participants:
        for target_id in participants:
            if source_id == target_id:
                continue
            seed = _initial_relationship_for_pair(
                source_id,
                target_id,
                char_types.get(source_id, ""),
                char_types.get(target_id, ""),
            )
            await ensure_relationship(
                source_id,
                target_id,
                rel_type=seed.get("type", "acquaintance"),
                affinity=int(seed.get("affinity", 0)),
                trust=int(seed.get("trust", 10)),
                current_status=seed.get("current_status", "first encounter"),
            )


# 기준 캐릭터와 성(姓)을 공유하는 부계 혈연 역할
_SAME_SURNAME_ROLES: frozenset[str] = frozenset({
    "남동생", "여동생", "형", "오빠", "언니", "누나",
    "아버지", "아빠", "할아버지",
})


async def _primary_name_for_id(char_id: str) -> str:
    """char_id 로 캐릭터의 대표 이름을 반환한다. 없으면 빈 문자열."""
    if not char_id:
        return ""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {id: $cid}) RETURN c.name AS name", cid=char_id
        )
        row = await rec.single()
    return str(row["name"]) if row and row["name"] else ""


async def _lookup_surname(name_part: str) -> str:
    """이름 일부로 캐릭터를 찾아 성(첫 글자)을 반환한다. 없으면 빈 문자열."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.name CONTAINS $partial
            RETURN c.name AS name
            ORDER BY size(c.name) ASC
            LIMIT 1
        """, partial=name_part)
        row = await rec.single()
    if row and row["name"] and re.match(r"^[가-힣]", row["name"]):
        return row["name"][0]
    return ""


async def _fetch_static_family_text(name_part: str) -> str:
    """Fetch StaticProfile.family text for the character matching a Korean name or alias."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_PROFILE]->(sp:StaticProfile)
            WHERE c.name CONTAINS $partial OR $partial IN c.aliases
            RETURN sp.props AS props_json
            ORDER BY size(c.name) ASC
            LIMIT 1
        """, partial=name_part)
        row = await rec.single()
    if not row or not row["props_json"]:
        return ""
    try:
        props = json.loads(row["props_json"])
    except (TypeError, json.JSONDecodeError):
        return ""
    family = props.get("family", "")
    return str(family).lower() if family else ""


def _sibling_role_from_family(family_text: str) -> str | None:
    """Infer the explicit sibling role described by a StaticProfile.family string."""
    if not family_text:
        return None
    if any(marker in family_text for marker in _NO_SIBLING_MARKERS):
        return "none"

    has_brother = any(marker in family_text for marker in _BROTHER_MARKERS)
    has_sister = any(marker in family_text for marker in _SISTER_MARKERS)
    if has_brother and not has_sister:
        return "\ub0a8\ub3d9\uc0dd"
    if has_sister and not has_brother:
        return "\uc5ec\ub3d9\uc0dd"
    return None


async def _normalize_relation_descriptor_for_family(name_kor: str) -> str | None:
    """Reject or normalize sibling descriptors that contradict an existing profile."""
    parsed = _parse_relation_descriptor(name_kor)
    if not parsed:
        return name_kor

    related_to, role = parsed
    if role not in _SIBLING_ROLE_SET:
        return name_kor

    expected = _sibling_role_from_family(await _fetch_static_family_text(related_to))
    if expected == "none":
        print(f"[WorldBuilder] sibling descriptor rejected by profile: {name_kor}")
        return None
    if expected and role == "\ub3d9\uc0dd":
        return f"{related_to} {expected}"
    if expected and role != expected:
        print(
            "[WorldBuilder] sibling descriptor rejected by profile: "
            f"{name_kor} (expected {expected})"
        )
        return None
    return name_kor


def _parse_relation_descriptor(value: str) -> tuple[str, str] | None:
    """Parse descriptors such as '유람의 남동생' or '유람 남동생'."""
    text = str(value or "").strip()
    patterns = (
        r"^([\uac00-\ud7a3]{2,4})\uc758\s*([\uac00-\ud7a3]+)$",
        r"^([\uac00-\ud7a3]{2,4})\s+([\uac00-\ud7a3]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        related_to, role = match.group(1), match.group(2)
        if _KINSHIP_DESCRIPTOR_RE.search(role):
            return related_to, role
    return None


def _is_usable_generated_name(generated: str, original: str) -> bool:
    """Return whether generated is a real Korean-style name, not a descriptor."""
    if not generated:
        return False
    if _parse_relation_descriptor(generated):
        return False
    if not _KOREAN_NAME_RE.match(generated):
        return False
    # original이 관계 서술어이고 generated가 그것과 같다면 이름으로 쓸 수 없다
    if generated == original and _parse_relation_descriptor(original):
        return False
    return True


def _is_stub_candidate(name_kor: str) -> bool:
    """Return whether text is concrete enough to persist as a transient character."""
    value = str(name_kor or "").strip()
    if _KOREAN_NAME_RE.match(value):
        return True
    return _parse_relation_descriptor(value) is not None


def _fallback_given_name(seed_text: str) -> str:
    """Pick a deterministic Korean given name from the descriptor."""
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[0]
    return _FALLBACK_GIVEN_NAMES[digest % len(_FALLBACK_GIVEN_NAMES)]


_SURNAME_POOL: tuple[str, ...] = tuple(_KOREAN_SURNAME_ROMAN.keys())

# 소유자가 특정되지 않는 일반 역할/직업 호칭. 이름 대신 등장하면 임의의 실제 이름을 만든다.
_GENERIC_ROLE_TOKENS: frozenset[str] = frozenset({
    "학생", "여학생", "남학생", "선생님", "교수님", "직원", "종업원", "점원",
    "알바", "아르바이트", "사장", "점장", "손님", "동료", "동급생", "의사",
    "간호사", "사서", "코치", "감독", "경호원", "운전기사", "담당", "관리자",
})


def _alt_surname(seed_text: str, exclude: str = "") -> str:
    """소유자와 다른 성을 결정론적으로 고른다(예: 엄마는 자식과 다른 성)."""
    pool = [s for s in _SURNAME_POOL if s != exclude] or list(_SURNAME_POOL)
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[1]
    return pool[digest % len(pool)]


def _compose_name(surname: str, seed_text: str) -> tuple[str, str]:
    """성 + 결정론적 이름으로 (한글 이름, 로마자 id base) 쌍을 만든다."""
    given = _fallback_given_name(seed_text)
    surname_roman = _KOREAN_SURNAME_ROMAN.get(surname[0] if surname else "김", "kim")
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[0]
    given_roman = _FALLBACK_GIVEN_NAMES_ROMAN[digest % len(_FALLBACK_GIVEN_NAMES_ROMAN)]
    return f"{surname}{given}", f"{surname_roman}_{given_roman}"


def _sentence_snippets_for_name(source_text: str, name_kor: str) -> list[str]:
    """Return short source sentences that explicitly mention the transient character token."""
    text = re.sub(r"\s+", " ", str(source_text or "")).strip()
    if not text or not name_kor:
        return []
    pieces = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", text)
    snippets: list[str] = []
    for piece in pieces:
        sentence = piece.strip()
        if not sentence or name_kor not in sentence:
            continue
        snippets.append(sentence[:220])
        if len(snippets) >= 3:
            break
    if snippets:
        return snippets
    index = text.find(name_kor)
    if index < 0:
        return []
    start = max(0, index - 80)
    end = min(len(text), index + len(name_kor) + 140)
    return [text[start:end].strip()]


def _snippet_with_markers(snippets: list[str], markers: tuple[str, ...]) -> str:
    """Return the first snippet containing any requested marker."""
    for snippet in snippets:
        if any(marker in snippet for marker in markers):
            return snippet
    return ""


def _stub_world_context(world_config: dict | None) -> str:
    """Return compact world/scenario text for plausible transient NPC defaults."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []
    for key, limit in (("world", 900), ("scenario", 1400)):
        value = str(sections.get(key) or "").strip()
        if value:
            parts.append(value[:limit])
    return "\n\n".join(parts) if parts else "(none)"


async def _fill_plausible_stub_fields(
    name_kor: str,
    stub: dict,
    snippets: list[str],
    world_config: dict | None,
) -> dict:
    """Fill empty transient NPC fields with plausible defaults without overriding evidence."""
    from src.core.llm.client import extract_json_from_llm, get_model

    observed = " / ".join(snippets) if snippets else "(no direct descriptive sentence)"
    # 키/몸무게/3-size 는 성별·나이·체형이 정해져야 개연성이 생기므로, 그 둘을 먼저 확정하도록 지시한다.
    prompt = f"""Create minimal plausible defaults for a newly mentioned transient NPC.

Anchoring rules (decide in this order):
1. Determine biological_sex and age first — these anchor every physical value.
2. Derive height (cm), weight (kg), measurements, and physique so they are mutually
   consistent and realistic for that sex, age, and build (e.g. a slender teenage girl
   and a heavyset middle-aged man must not share the same numbers).
3. measurements: for female use "B-W-H" in cm (e.g. "84-60-88"); for male use chest/waist
   in cm or leave blank if unnatural to state. physique: one short build descriptor
   (e.g. "마른", "보통", "탄탄한", "통통한").

Constraints:
- Preserve observed evidence exactly. Do not contradict it.
- Fill only missing fields. Do not overwrite observed fields.
- If appearance is observed, reuse it; otherwise invent a generic plausible appearance
  consistent with the anchored sex/age/build.
- If relationship/role is observed, reuse it; otherwise invent only a low-detail plausible role/status.
- Do not create secrets, durable biography, trauma, special skills, or strong personality unless evidence says so.
- Korean is OK. Return concise field values.

Character token: {name_kor}
Observed evidence: {observed}
Existing stub:
{json.dumps(stub, ensure_ascii=False)}

World/scenario context:
{_stub_world_context(world_config)}

Return ONLY JSON with optional fields:
{{
  "biological_sex": "",
  "age": "",
  "height": "",
  "weight": "",
  "measurements": "",
  "physique": "",
  "appearance": "",
  "family": "",
  "formative_background": "",
  "initial_mood": "",
  "personality": "",
  "speech_style": "",
  "relation_type": "",
  "relation_status": "",
  "initial_affinity": 0
}}"""

    try:
        model = get_model(
            STUB_MODEL,
            system_prompt="Generate conservative, internally consistent defaults for transient roleplay NPC records.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.35,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json",
                "log_source": "transient_npc_stub",
            },
        )
        parsed = extract_json_from_llm(resp.text, source="transient_npc_stub")
    except Exception as exc:
        print(f"[WorldBuilder] transient stub default generation failed: {exc}")
        return stub

    if not isinstance(parsed, dict):
        return stub

    # LLM 출력을 StubProfile로 검증/정규화한 뒤, 관찰 증거(이미 채워진 stub 필드)는 덮어쓰지 않는다.
    try:
        defaults = StubProfile.model_validate(parsed)
    except Exception as exc:
        print(f"[WorldBuilder] transient stub validation failed: {exc}")
        return stub

    merged = dict(stub)
    for key in (
        "biological_sex",
        "age",
        "height",
        "weight",
        "measurements",
        "physique",
        "appearance",
        "family",
        "formative_background",
        "initial_mood",
        "personality",
        "speech_style",
        "relation_type",
        "relation_status",
    ):
        if str(merged.get(key) or "").strip():
            continue
        value = str(getattr(defaults, key) or "").strip()
        if value:
            merged[key] = value[:500]
    if not merged.get("initial_affinity"):
        merged["initial_affinity"] = defaults.initial_affinity
    return merged


async def _build_conservative_stub_profile(
    name_kor: str,
    main_npc_id: str,
    source_text: str = "",
    world_config: dict | None = None,
) -> dict:
    """Build a transient NPC stub from observed text, filling unknowns plausibly."""
    parsed = _parse_relation_descriptor(name_kor)
    # 소유격 없이 호칭만 등장한 경우(예: '아빠','엄마') → 현재 메인 NPC의 가족으로 간주하고
    # 그 인물을 소유자로 삼아 이름을 생성한다. 부계 호칭(아빠/형 등)은 같은 성을 쓰고,
    # 엄마/어머니는 _SAME_SURNAME_ROLE_SET 에서 빠져 있어 자연히 다른 성이 부여된다.
    if parsed is None and _KINSHIP_DESCRIPTOR_RE.fullmatch(str(name_kor or "")):
        owner_name = await _primary_name_for_id(main_npc_id)
        if owner_name:
            parsed = (owner_name, name_kor)
    related_to, role = parsed if parsed else ("", name_kor)
    snippets = _sentence_snippets_for_name(source_text, name_kor)
    observed_context = " / ".join(snippets[:2])
    appearance = _snippet_with_markers(snippets, _APPEARANCE_MARKERS)
    role_evidence = _snippet_with_markers(snippets, _ROLE_MARKERS)
    if parsed:
        surname = ""
        if role in _SAME_SURNAME_ROLE_SET:
            surname = await _lookup_surname(related_to)
        if not surname:
            # \uac19\uc740 \uc131\uc774 \uc544\ub2cc \ud638\uce6d(\uc5c4\ub9c8/\uc5b4\uba38\ub2c8 \ub4f1)\uc740 \uc18c\uc720\uc790\uc640 \ub2e4\ub978 \uc131\uc744 \ubd80\uc5ec\ud55c\ub2e4.
            owner_surname = related_to[0] if related_to and "\uac00" <= related_to[0] <= "\ud7a3" else ""
            surname = _alt_surname(name_kor, owner_surname)
        generated_name = f"{surname}{_fallback_given_name(name_kor)}"
        surname_roman = _KOREAN_SURNAME_ROMAN.get(surname[0] if surname else "\uae40", "kim")
        digest = hashlib.sha1(name_kor.encode("utf-8")).digest()[0]
        given_roman = _FALLBACK_GIVEN_NAMES_ROMAN[digest % len(_FALLBACK_GIVEN_NAMES_ROMAN)]
        name_roman = f"{surname_roman}_{given_roman}"
        context = f"{related_to}의 {role} 관계로 언급되어 처음 인식된 인물."
        relation_type = "family" if role in _FAMILY_ROLE_SET else "acquaintance"
    elif name_kor in _GENERIC_ROLE_TOKENS:
        # 소유자 없는 일반 역할 호칭(여학생/선생님 등)은 임의의 실제 이름을 생성한다.
        generated_name, name_roman = _compose_name(_alt_surname(name_kor), name_kor)
        context = f"'{name_kor}' role; first appearance."
        relation_type = "acquaintance"
    else:
        generated_name = name_kor
        name_roman = _kor_to_roman_id(name_kor)
        context = f"{name_kor}로 명시적으로 언급되어 처음 인식된 인물."
        relation_type = "acquaintance"
    if observed_context:
        context = f"{context} Observed evidence: {observed_context}"

    stub = {
        "name_kor":             generated_name,
        "name_roman":           name_roman,
        "biological_sex":       "",
        "age":                  "",
        "height":               "",
        "weight":               "",
        "measurements":         "",
        "physique":             "",
        "appearance":           appearance,
        "family":               "",
        "formative_background": "",
        "initial_mood":         "",
        "personality":          "",
        "speech_style":         "",
        "context":              context,
        "relation_type":        relation_type,
        "relation_status":      role_evidence,
        "initial_affinity":     0,
    }
    return await _fill_plausible_stub_fields(name_kor, stub, snippets, world_config)


async def _increment_appearance(char_id: str) -> None:
    """StaticProfile JSON blob의 appearance_count를 1 증가시킨다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            RETURN sp.props AS props_json
        """, cid=char_id)
        row = await rec.single()
        current: dict = {}
        if row and row["props_json"]:
            try:
                current = json.loads(row["props_json"])
            except (ValueError, TypeError):
                pass
        current["appearance_count"] = int(current.get("appearance_count") or 0) + 1
        current["last_seen"] = datetime.now().isoformat()
        await session.run("""
            MATCH (c:Character {id: $cid})-[:HAS_PROFILE]->(sp:StaticProfile)
            SET sp.props = $props_json
        """, cid=char_id, props_json=json.dumps(current, ensure_ascii=False))


async def _link_to_event(char_id: str, event_id: str) -> None:
    """캐릭터와 이벤트를 INVOLVED_IN 관계로 연결한다. 중복 방지."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $cid})-[:INVOLVED_IN]->(e:Event {id: $eid})
            RETURN e.id AS id
        """, cid=char_id, eid=event_id)
        if await rec.single():
            return
        await session.run("""
            MATCH (c:Character {id: $cid}), (e:Event {id: $eid})
            CREATE (c)-[:INVOLVED_IN]->(e)
        """, cid=char_id, eid=event_id)
