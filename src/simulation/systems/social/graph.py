# ================================
# src/simulation/systems/social/graph.py
#
# Character identity resolution, transient NPC creation, and event linking for the Social system.
#
# Functions
#   - _cache_key() -> str : Return the current session's cache key (db_path or '__global__')
#   - _get_known_chars() -> dict[str, str] : Fetch character names and aliases (per-session cached)
#   - _invalidate_cache() -> None : Invalidate the current session's character cache
#   - _resolve_identity(name: str, known: dict[str, str]) -> str | None : Resolve a name to char_id
#   - _create_stub(name_kor: str, main_npc_id: str, pc_id: str, world_config: dict) -> str | None : Create a transient NPC
#   - _normalize_relation_descriptor_for_family(name_kor: str) -> str | None : Validate sibling descriptors against StaticProfile.family
#   - _increment_appearance(char_id: str) -> None : Increment appearance_count
#   - _link_to_event(char_id: str, event_id: str) -> None : Link a character to an event
#   - _kor_to_roman_id(name_kor: str) -> str : Hash-based fallback romanization for Korean names
# ================================
import hashlib
import json
import re
from datetime import datetime

from src.config import MODEL_EVENT_CREATOR as STUB_MODEL
from src.core.database import async_driver
from src.core.database.helpers import ensure_relationship
from src.core.llm.client import get_model, extract_json_from_llm

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

def _cache_key() -> str:
    """현재 Chainlit 세션의 db_path를 캐시 키로 반환한다. 세션 외부면 '__global__'."""
    try:
        import chainlit as cl
        return cl.user_session.get("db_path") or "__global__"
    except Exception:
        return "__global__"


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


def _invalidate_cache() -> None:
    """현재 세션의 캐릭터 캐시를 무효화한다."""
    _known_chars_cache.pop(_cache_key(), None)


def _resolve_identity(name: str, known: dict[str, str]) -> str | None:
    """이름이 known dict에 있으면 char_id 반환, 없으면 None."""
    if name in known:
        return known[name]
    if _KINSHIP_DESCRIPTOR_RE.search(name):
        return None
    if len(name) >= 2:
        for k, v in known.items():
            if name in k or k in name:
                return v
    return None


def _kor_to_roman_id(name_kor: str) -> str:
    """한국어 이름에서 타임스탬프 기반 영문 char_id를 생성한다."""
    ts   = datetime.now().strftime("%m%d%H%M%S")
    safe = re.sub(r'[^a-z0-9]', '', name_kor.encode('ascii', 'ignore').decode())
    if not safe:
        safe = "npc"
    name_hash = hashlib.sha1(name_kor.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{name_hash}_{ts}"


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
) -> str | None:
    """Transient NPC stub 생성. char_id 반환."""
    original_name_kor = name_kor
    normalized_name = await _normalize_relation_descriptor_for_family(name_kor)
    if not normalized_name:
        return None
    name_kor = normalized_name

    # 이름 또는 aliases로 이미 존재하는지 확인
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.name = $name OR c.name = $original_name
               OR $name IN c.aliases OR $original_name IN c.aliases
            RETURN c.id AS id
        """, name=name_kor, original_name=original_name_kor)
        row = await rec.single()
        if row:
            return row["id"]

    stub = await _generate_stub_profile(name_kor, world_config, main_npc_id)
    if not isinstance(stub, dict):
        stub = await _fallback_stub_profile(name_kor, main_npc_id)

    # LLM이 생성한 이름을 우선 사용하고, 실패하면 관계 지칭에 맞는 한국식 이름을 만든다.
    generated = (stub.get("name_kor") or "").strip()
    if _is_usable_generated_name(generated, name_kor):
        display_name = generated
    else:
        fallback_stub = await _fallback_stub_profile(name_kor, main_npc_id)
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

        # DynamicInformation — stub에서 얻은 물리 정보 취합
        info_props = json.dumps({
            "height":     stub.get("height", ""),
            "weight":     stub.get("weight", ""),
            "appearance": stub.get("appearance", ""),
            "summary":    stub.get("context") or "No durable dynamic information recorded yet.",
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


def _fallback_given_name(seed_text: str) -> str:
    """Pick a deterministic Korean given name from the descriptor."""
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[0]
    return _FALLBACK_GIVEN_NAMES[digest % len(_FALLBACK_GIVEN_NAMES)]


async def _fallback_stub_profile(name_kor: str, main_npc_id: str) -> dict:
    """Build a deterministic fallback stub when the LLM name generator is unavailable."""
    parsed = _parse_relation_descriptor(name_kor)
    related_to, role = parsed if parsed else ("", name_kor)
    surname = ""
    if related_to and role in _SAME_SURNAME_ROLE_SET:
        surname = await _lookup_surname(related_to)
    if not surname:
        surname = "\uae40"

    generated_name = f"{surname}{_fallback_given_name(name_kor)}"
    context = (
        f"{related_to}의 {role} 관계로 언급되어 처음 인식된 인물."
        if related_to
        else f"{name_kor}로 언급되어 처음 인식된 인물."
    )

    # 성씨 로마자 + 이름 로마자로 fallback char_id 구성
    surname_roman = _KOREAN_SURNAME_ROMAN.get(surname[0] if surname else "\uae40", "kim")
    digest = hashlib.sha1(name_kor.encode("utf-8")).digest()[0]
    given_roman = _FALLBACK_GIVEN_NAMES_ROMAN[digest % len(_FALLBACK_GIVEN_NAMES_ROMAN)]
    name_roman = f"{surname_roman}_{given_roman}"

    return {
        "name_kor":             generated_name,
        "name_roman":           name_roman,
        "biological_sex":       "",
        "age":                  "",
        "height":               "",
        "weight":               "",
        "appearance":           "",
        "family":               "",
        "formative_background": "",
        "initial_mood":         "calm",
        "personality":          "unknown",
        "speech_style":         "",
        "context":              context,
        "relation_type":        "family" if role in _SAME_SURNAME_ROLE_SET else "acquaintance",
        "relation_status":      f"first known as {name_kor}; not yet personally established with {main_npc_id}",
        "initial_affinity":     0,
    }


async def _generate_stub_profile(
    name_kor:    str,
    world_config: dict,
    main_npc_id: str,
) -> dict | list | None:
    """Haiku 1회 — stub 프로필 초안 생성."""
    world_ctx = world_config.get("world_section", "")[:300]

    # 관계 서술어 파싱: "A의 남동생"/"A 남동생" → related_to="A", role="남동생"
    parsed = _parse_relation_descriptor(name_kor)
    related_to, role = parsed if parsed else ("", name_kor)

    # Determine surname constraint and pass it to the LLM in English
    surname_rule = "Choose a natural Korean surname that fits the world."
    if related_to and role in _SAME_SURNAME_ROLE_SET:
        surname = await _lookup_surname(related_to)
        if surname:
            surname_rule = f'Must start with "{surname}" — paternal blood relative (shares the same father).'
    elif related_to:
        surname = await _lookup_surname(related_to)
        if surname:
            surname_rule = f'Must NOT start with "{surname}" — not a paternal blood relative (e.g. mother, in-law, friend).'

    system_instruction = (
        "You generate minimal NPC stubs for a Korean slice-of-life roleplay. "
        "Output JSON only. No extra text."
    )

    prompt = json.dumps({
        "world":          world_ctx,
        "reference_npc":  main_npc_id,
        "character_hint": {
            "value": name_kor,
            "note":  "Proper name → keep as-is. 'X의 Y' descriptor → invent a new person for role Y.",
        },
        "surname_rule": surname_rule,
        "output_schema": {
            "name_kor":            "Korean full name — surname_rule is mandatory",
            "name_roman":          "romanized char_id: lowercase surname_givenname (e.g. han_yuram, kim_minjun)",
            "biological_sex":      "Male or Female",
            "age":                 "integer: estimated age",
            "height":              "e.g. 170cm",
            "weight":              "e.g. 60kg",
            "appearance":          "1 Korean sentence: physical features",
            "personality":         "2-3 English adjectives, +-separated",
            "speech_style":        "brief Korean: honorific level + tone (e.g. '반말, 조용한 어조')",
            "context":             "1 Korean sentence: who this person is",
            "family":              "1 Korean sentence: family composition",
            "formative_background": "1 Korean sentence: key formative experience",
            "initial_mood":        "calm | cheerful | tired | tense | melancholic",
            "relation_type":       "acquaintance | classmate | coworker | customer | stranger | family",
            "relation_status":     "1 English sentence: current standing with reference_npc",
            "initial_affinity":    0,
        },
        "example_output": {
            "name_kor":            "박하름",
            "name_roman":          "park_harum",
            "biological_sex":      "Male",
            "age":                 16,
            "height":              "168cm",
            "weight":              "58kg",
            "appearance":          "폭이 넓은 어깨와 늘차 맑는 눈을 가진 평범한 테자 마리의 소년.",
            "personality":         "quiet+caring+stubborn",
            "speech_style":        "반말, 조용한 어조",
            "context":             "주인공의 두 살 아래 남동생으로 고등학교 2학년.",
            "family":              "부모와 함께 사는 정상적인 가정.",
            "formative_background": "중학교 때 축구부에서 활동해 팀워크를 배웠다.",
            "initial_mood":        "calm",
            "relation_type":       "acquaintance",
            "relation_status":     "younger sibling — close but occasionally bickers",
            "initial_affinity":    0,
        },
    }, ensure_ascii=False)

    try:
        model = get_model(STUB_MODEL, system_prompt=system_instruction)
        resp = await model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        return extract_json_from_llm(resp.text)
    except Exception as e:
        print(f"[WorldBuilder] stub 생성 실패: {e}")
        return None


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
