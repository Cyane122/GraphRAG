# ================================
# src/core/database/helpers.py
#
# Kuzu 공통 쓰기/읽기 헬퍼 함수 모음입니다.
#
# Functions
#   - update_dynamic_information(char_id: str, updates: dict) -> None : DynamicInformation props JSON update
#   - ensure_location(location_id: str | None, name: str, description: str = "", prompt_hint: str = "", parent_location_id: str | None = None, tags: list[str] | None = None, prompt_priority: int = 8) -> str : Location node create/update helper
#   - update_dynamic_state(char_id: str, updates: dict) -> None : DynamicState 노드 속성 공통 업데이트
#   - update_relationship_affinity(char_a: str, char_b: str, delta: int) -> None : 호감도 공통 업데이트 (양방향, ±100 상한)
#   - _compact_relationship_status(value: object) -> str | None : Remove scene-action detail from RELATIONSHIP current_status.
#   - move_location(char_id: str, new_loc_id: str) -> None : 캐릭터 장소 이동 공통 로직
#   - advance_cycle_day(char_id: str, days: int) -> None : 생리/바이오리듬 일자 공통 업데이트
#   - get_in_universe_time() -> str : GlobalState에서 현재 인게임 시간을 YYYYMMDD_HHMM 형식으로 반환
#   - load_graph_info() -> dict : 그래프 현재 상태(전역·캐릭터·장소·관계)를 dict로 반환
# ================================

import hashlib
import json
import re
from datetime import datetime

from src.core.database import async_driver
from src.core.state_normalization import normalize_stress_level


DYNAMIC_STATE_FIELDS = {
    "id",
    "physical_condition", "mental_condition", "stress_level", "mood",
    "cycle_day", "location_id", "workplace_stress_level",
    "knee_condition", "injury_detail", "energy", "stress",
    "current_task",
    "outfit", "injury_marks",
    "pregnant", "pregnancy_day", "cum_shots_this_cycle", "has_menstrual_cycle",
    "ts_acceptance", "northern_attachment", "body_perception", "behavioral_facade",
    "hygiene", "appearance", "physique", "age_presentation", "nervousness",
    "attitude", "social_skill", "consideration", "stamina", "odor",
    "emotional_state", "attachment_risk", "expectation_gap", "penis_size",
    "age", "circle_level", "robe_grade",
}

# LLM이 스키마에 존재하는 컬럼명으로 잘못 반환하더라도 쓰지 않을 필드.
# condition → injury_detail/physical_condition 중복, current_location → location_id 중복.
_DYNAMIC_STATE_WRITE_BLOCKLIST: frozenset[str] = frozenset({"condition", "current_location"})

DYNAMIC_STATE_INT_FIELDS = {
    "stress_level", "cycle_day", "workplace_stress_level",
    "pregnancy_day", "cum_shots_this_cycle",
    "ts_acceptance", "northern_attachment",
    "age", "circle_level",
}

DYNAMIC_STATE_FLOAT_FIELDS = {
    "energy", "stress", "hygiene", "appearance", "nervousness",
    "social_skill", "consideration", "stamina", "attachment_risk",
    "expectation_gap",
}

DYNAMIC_STATE_BOOL_FIELDS = {"pregnant"}

DYNAMIC_INFORMATION_FIELDS = {
    "age",
    "grade_class",
    "height",
    "weight",
    "measurements",
    "appearance",
    "personality",
    "skills",
    "current_reputation",
    "hobby",
    "sexual_information",
    "current_status",
    "prompt_hint",
    "summary",
}


def _slug_location_id(value: str) -> str:
    """Location 이름/ID 후보를 DB에서 쓰기 쉬운 ascii id로 정규화합니다."""
    slug = re.sub(r"[^a-z0-9_]+", "_", str(value).lower()).strip("_")
    if slug:
        return slug[:80]
    digest = hashlib.blake2s(str(value).encode("utf-8"), digest_size=5).hexdigest()
    return f"loc_{digest}"


def _coerce_prompt_priority(value: object, default: int = 8) -> int:
    """Location prompt_priority 값을 안전한 정수로 정규화합니다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object) -> int | None:
    """Kuzu INT64 필드에 바인딩할 값을 int로 변환합니다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    """Kuzu DOUBLE 필드에 바인딩할 값을 float로 변환합니다."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: object) -> bool | None:
    """Kuzu BOOLEAN 필드에 바인딩할 값을 bool로 변환합니다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    return None


async def get_dynamic_state_field_types() -> dict[str, str]:
    """Return DynamicState column names and Kuzu types from the live schema."""
    async with async_driver.session() as session:
        result = await session.run("CALL table_info('DynamicState') RETURN name, type")
        rows = await result.fetch_all()
    return {
        str(row["name"]): str(row["type"]).upper()
        for row in rows
        if row["name"]
    }


def _normalize_dynamic_state_updates(
    updates: dict,
    field_types: dict[str, str] | None = None,
) -> dict:
    """DynamicState 업데이트 값을 Kuzu 스키마 타입에 맞게 정규화합니다."""
    allowed_fields = set(field_types or DYNAMIC_STATE_FIELDS)
    normalized = {}
    for field, value in updates.items():
        if field not in allowed_fields or field == "id" or field in _DYNAMIC_STATE_WRITE_BLOCKLIST:
            continue
        field_type = (field_types or {}).get(field, "").upper()
        if field in DYNAMIC_STATE_INT_FIELDS or field_type.startswith("INT"):
            int_value = (
                normalize_stress_level(value)
                if field in {"stress_level", "workplace_stress_level"}
                else _coerce_int(value)
            )
            if int_value is not None:
                normalized[field] = int_value
            continue
        if field in DYNAMIC_STATE_FLOAT_FIELDS or field_type in {"DOUBLE", "FLOAT"}:
            float_value = _coerce_float(value)
            if float_value is not None:
                normalized[field] = float_value
            continue
        if field in DYNAMIC_STATE_BOOL_FIELDS or field_type in {"BOOL", "BOOLEAN"}:
            bool_value = _coerce_bool(value)
            if bool_value is not None:
                normalized[field] = bool_value
            continue
        normalized[field] = value
    return normalized


async def update_dynamic_state(char_id: str, updates: dict) -> None:
    """DynamicState 노드 속성 공통 업데이트."""
    if not updates:
        return
    field_types = await get_dynamic_state_field_types()
    updates = _normalize_dynamic_state_updates(updates, field_types=field_types)
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    async with async_driver.session() as session:
        await session.run(
            f"MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState) SET {set_clause}",
            char_id=char_id, **updates,
        )


def _normalize_dynamic_information_updates(updates: dict) -> dict:
    """DynamicInformation JSON props에 병합 가능한 필드만 정리합니다."""
    normalized = {}
    for field, value in updates.items():
        if value in (None, "", [], {}):
            continue
        normalized[field] = value
    return normalized


async def update_dynamic_information(char_id: str, updates: dict) -> None:
    """DynamicInformation props JSON을 기존 값 위에 병합해서 저장합니다."""
    updates = _normalize_dynamic_information_updates(updates)
    if not updates:
        return

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_INFO]->(n:DynamicInformation)
            RETURN n.id AS id, n.props AS props
        """, char_id=char_id)
        row = await rec.single()

        if row:
            try:
                current = json.loads(row["props"] or "{}")
            except (TypeError, ValueError):
                current = {}
            current.update(updates)
            await session.run(
                "MATCH (n:DynamicInformation {id: $id}) SET n.props = $props",
                id=row["id"],
                props=json.dumps(current, ensure_ascii=False),
            )
            return

        node_id = f"{char_id}_info"
        await session.run("""
            MATCH (c:Character {id: $char_id})
            CREATE (c)-[:HAS_INFO]->(:DynamicInformation {id: $node_id, props: $props})
        """, char_id=char_id, node_id=node_id, props=json.dumps(updates, ensure_ascii=False))


async def ensure_location(
    location_id: str | None,
    name: str,
    description: str = "",
    prompt_hint: str = "",
    parent_location_id: str | None = None,
    tags: list[str] | None = None,
    prompt_priority: int = 8,
) -> str:
    """Location 노드를 생성하거나 기존 노드를 갱신하고 Location id를 반환합니다."""
    loc_id = _slug_location_id(location_id or name)
    loc_name = str(name or loc_id).strip() or loc_id
    loc_tags = [str(tag) for tag in (tags or []) if str(tag).strip()]
    loc_priority = _coerce_prompt_priority(prompt_priority)

    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (l:Location {id: $id}) RETURN l.id AS id",
            id=loc_id,
        )
        exists = await rec.single() is not None

        if exists:
            await session.run(
                """
                MATCH (l:Location {id: $id})
                SET l.name = $name,
                    l.description = $description,
                    l.prompt_hint = $prompt_hint,
                    l.prompt_priority = $prompt_priority,
                    l.tags = $tags
                """,
                id=loc_id,
                name=loc_name,
                description=description or "",
                prompt_hint=prompt_hint or description or "",
                prompt_priority=loc_priority,
                tags=loc_tags,
            )
        else:
            await session.run(
                """
                CREATE (:Location {
                    id: $id,
                    name: $name,
                    description: $description,
                    prompt_hint: $prompt_hint,
                    prompt_priority: $prompt_priority,
                    tags: $tags
                })
                """,
                id=loc_id,
                name=loc_name,
                description=description or "",
                prompt_hint=prompt_hint or description or "",
                prompt_priority=loc_priority,
                tags=loc_tags,
            )

        if parent_location_id:
            parent_rec = await session.run(
                "MATCH (p:Location {id: $id}) RETURN p.id AS id",
                id=parent_location_id,
            )
            if await parent_rec.single():
                await session.run(
                    """
                    MATCH (l:Location {id: $loc_id}), (p:Location {id: $parent_id})
                    MERGE (l)-[:PART_OF]->(p)
                    """,
                    loc_id=loc_id,
                    parent_id=parent_location_id,
                )

    return loc_id


async def update_relationship_affinity(char_a: str, char_b: str, delta: int) -> None:
    """호감도 공통 업데이트 (양방향, ±100 상한). trust < 90이면 affinity 증가 차단."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.affinity = CASE
                WHEN $delta > 0 AND coalesce(r.trust, 0) < 90 THEN coalesce(r.affinity, 0)
                WHEN coalesce(r.affinity, 0) + $delta > 100   THEN 100
                WHEN coalesce(r.affinity, 0) + $delta < -100  THEN -100
                ELSE coalesce(r.affinity, 0) + $delta
            END
        """, a=char_a, b=char_b, delta=delta)


RELATIONSHIP_FIELDS = {"type", "affinity", "trust", "current_status", "summary", "last_interaction"}
RELATIONSHIP_INT_FIELDS = {"affinity", "trust"}
_SCENE_ACTION_STATUS_RE = re.compile(
    r"^\s*(currently|now|right now|at the moment|during this scene|in this scene|"
    r"현재|지금|이번\s*장면|이\s*장면|그\s*순간)\b",
    re.IGNORECASE,
)
_SCENE_ACTION_PHRASE_RE = re.compile(
    r"\b(currently|now|right now|at the moment)\b.*\b("
    r"doing|having|sitting|standing|lying|kissing|touching|talking|arguing|"
    r"walking|eating|drinking|wearing|holding|moving|waiting)\b",
    re.IGNORECASE,
)


def _clamp_relationship_score(value: object) -> int | None:
    """Normalize affinity/trust values to the RELATIONSHIP score range."""
    raw_value = _coerce_int(value)
    if raw_value is None:
        return None
    return max(-100, min(100, raw_value))


def _compact_relationship_status(value: object) -> str | None:
    """Remove scene-action detail from RELATIONSHIP current_status."""
    if not isinstance(value, str):
        return None

    sentences = re.split(r"(?<=[.!?。])\s+", value.strip())
    durable_sentences = [
        sentence.strip()
        for sentence in sentences
        if (
            sentence.strip()
            and not _SCENE_ACTION_STATUS_RE.search(sentence)
            and not _SCENE_ACTION_PHRASE_RE.search(sentence)
        )
    ]
    compacted = " ".join(durable_sentences).strip()
    return compacted or None


def _normalize_relationship_updates(updates: dict) -> dict:
    """Keep only safe RELATIONSHIP fields and coerce numeric values."""
    normalized = {}
    for field, value in updates.items():
        if field not in RELATIONSHIP_FIELDS or value in (None, ""):
            continue
        if field in RELATIONSHIP_INT_FIELDS:
            score = _clamp_relationship_score(value)
            if score is not None:
                normalized[field] = score
            continue
        if field == "current_status":
            compacted = _compact_relationship_status(value)
            if compacted:
                normalized[field] = compacted
            continue
        normalized[field] = str(value)
    return normalized


async def ensure_relationship(
    char_a: str,
    char_b: str,
    rel_type: str = "acquaintance",
    affinity: int = 0,
    trust: int = 10,
    current_status: str = "first encounter",
) -> None:
    """Create a directed RELATIONSHIP edge when two distinct characters lack one."""
    if not char_a or not char_b or char_a == char_b:
        return

    normalized_affinity = _clamp_relationship_score(affinity)
    normalized_trust = _clamp_relationship_score(trust)
    affinity = normalized_affinity if normalized_affinity is not None else 0
    trust = normalized_trust if normalized_trust is not None else 10
    async with async_driver.session() as session:
        rec = await session.run(
            """
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            RETURN r.type AS type
            """,
            a=char_a,
            b=char_b,
        )
        if await rec.single():
            return

        await session.run(
            """
            MATCH (a:Character {id: $a}), (b:Character {id: $b})
            CREATE (a)-[:RELATIONSHIP {
                type: $rel_type,
                affinity: $affinity,
                trust: $trust,
                current_status: $current_status
            }]->(b)
            """,
            a=char_a,
            b=char_b,
            rel_type=rel_type or "acquaintance",
            affinity=affinity,
            trust=trust,
            current_status=current_status or "first encounter",
        )


async def update_relationship_fields(char_a: str, char_b: str, updates: dict) -> None:
    """Update safe RELATIONSHIP properties for an existing directed edge."""
    updates = _normalize_relationship_updates(updates)
    if not updates:
        return
    set_clause = ", ".join(f"r.{field} = ${field}" for field in updates)
    async with async_driver.session() as session:
        await session.run(
            f"""
            MATCH (a:Character {{id: $a}})-[r:RELATIONSHIP]->(b:Character {{id: $b}})
            SET {set_clause}
            """,
            a=char_a,
            b=char_b,
            **updates,
        )


async def move_location(char_id: str, new_loc_id: str) -> None:
    """캐릭터 장소 이동 공통 로직. LOCATED_AT 관계 + DynamicState.location_id 양쪽을 동기화한다."""
    async with async_driver.session() as session:
        check_rec = await session.run(
            "MATCH (l:Location {id: $new_loc_id}) RETURN l.id AS id",
            new_loc_id=new_loc_id,
        )
        if not await check_rec.single():
            print(f"[move_location] invalid location ignored: {new_loc_id}")
            return

        await session.run("""
            MATCH (c:Character {id: $char_id})-[old:LOCATED_AT]->(:Location)
            DELETE old
        """, char_id=char_id)

        await session.run("""
            MATCH (c:Character {id: $char_id}), (next:Location {id: $new_loc_id})
            CREATE (c)-[:LOCATED_AT]->(next)
        """, char_id=char_id, new_loc_id=new_loc_id)

        try:
            await session.run("""
                MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
                SET d.location_id = $new_loc_id
            """, char_id=char_id, new_loc_id=new_loc_id)
        except Exception as _loc_sync_err:
            print(f"[move_location] DynamicState.location_id sync skipped: {_loc_sync_err}")


async def advance_cycle_day(char_id: str, days: int) -> None:
    """생리/바이오리듬 일자 공통 업데이트."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState)
            WHERE d.cycle_day IS NOT NULL
            SET d.cycle_day = ((d.cycle_day + $days - 1) % 28) + 1
        """, char_id=char_id, days=days)


async def get_in_universe_time() -> str:
    """GlobalState에서 현재 인게임 시간을 YYYYMMDD_HHMM 형식으로 반환합니다."""
    async with async_driver.session() as session:
        result = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct"
        )
        row = await result.single()
        if row and row["ct"]:
            return datetime.fromisoformat(row["ct"]).strftime("%Y%m%d_%H%M")
    return "20240101_0000"


async def load_graph_info() -> dict:
    """
    그래프 현재 상태를 dict로 반환합니다.

    반환 구조:
        global_state : GlobalState 싱글톤 필드
        characters   : 캐릭터별 {id, name, type, dynamic_state} 리스트
        locations    : Location 노드 {id, name, current_chars(LOCATED_AT 기반)} 리스트
        relationships: 관계 {from, to, affinity, trust} 리스트
    """
    async with async_driver.session() as session:
        # 1. 전역 상태
        gs_result = await session.run(
            "MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.*"
        )
        gs_row = await gs_result.single()
        global_state = dict(gs_row._data) if gs_row else {}

        # 2. 캐릭터 + 동적 상태
        char_result = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            RETURN c.id AS id, c.name AS name, c.type AS type,
                   d.mood AS mood, d.stress_level AS stress_level,
                   d.physical_condition AS physical_condition,
                   d.mental_condition AS mental_condition,
                   d.location_id AS location_id
        """)
        characters = []
        for row in await char_result.fetch_all():
            characters.append({
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "dynamic_state": {
                    "mood": row["mood"],
                    "stress_level": row["stress_level"],
                    "physical_condition": row["physical_condition"],
                    "mental_condition": row["mental_condition"],
                    "location_id": row["location_id"],
                },
            })

        # 3. 장소 — LOCATED_AT 관계에서 현재 거주자를 조회한다
        loc_result = await session.run("""
            MATCH (l:Location)
            OPTIONAL MATCH (c:Character)-[:LOCATED_AT]->(l)
            RETURN l.id AS id, l.name AS name, collect(c.id) AS current_chars
        """)
        locations = []
        for row in await loc_result.fetch_all():
            locations.append({
                "id": row["id"],
                "name": row["name"],
                "current_chars": row["current_chars"] or [],
            })

        # 4. 관계
        rel_result = await session.run("""
            MATCH (a:Character)-[r:RELATIONSHIP]->(b:Character)
            RETURN a.id AS from_id, b.id AS to_id,
                   r.affinity AS affinity, r.trust AS trust
        """)
        relationships = []
        for row in await rel_result.fetch_all():
            relationships.append({
                "from": row["from_id"],
                "to": row["to_id"],
                "affinity": row["affinity"],
                "trust": row["trust"],
            })

    return {
        "global_state": global_state,
        "characters": characters,
        "locations": locations,
        "relationships": relationships,
    }
