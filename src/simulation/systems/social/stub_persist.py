# ================================
# src/simulation/systems/social/stub_persist.py
#
# Persist transient NPCs and maintain Social-system character appearance and event links.
#
# Functions
#   - _create_stub(name_kor: str, main_npc_id: str, pc_id: str, world_config: dict, allow_existing_alias_match: bool = True, source_text: str = "") -> str | None : Create a conservative transient NPC
#   - ensure_character_runtime_nodes(char_id: str) -> None : Ensure runtime nodes for durable scene participants
# ================================
import json
import re
from datetime import datetime

from src.core.database import async_driver
from src.core.database.helpers import ensure_relationship, update_dynamic_state
from src.simulation.systems.social.identity import _coerce_int, _fallback_identity_for_reference, _initial_cycle_day, _is_female_sex, _normalize_reference_kind, _requires_generated_name
from src.simulation.systems.social.naming import _kor_to_roman_id
from src.simulation.systems.social.stub_profile import _build_conservative_stub_profile, _is_stub_candidate, _is_usable_generated_name, _normalize_relation_descriptor_for_family

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

    reference_kind = _normalize_reference_kind(stub.get("reference_kind"))
    source_token = str(stub.get("source_token") or name_kor).strip()
    # 관계/역할 서술어는 고유 이름만 표시 이름으로 쓰고, 원문 토큰은 별칭/맥락에 남긴다.
    generated = (stub.get("name_kor") or "").strip()
    if (
        _is_usable_generated_name(generated, name_kor)
        and not (_requires_generated_name(reference_kind) and generated == source_token)
    ):
        display_name = generated
    else:
        display_name, fallback_roman = _fallback_identity_for_reference(source_token)
        stub = {**stub, "name_kor": display_name, "name_roman": fallback_roman}
    # 서술어('유람의 남동생')와 실제 이름이 다를 때 서술어를 aliases에 보관 (다음 턴 재인식용)
    aliases = []
    for alias in (original_name_kor, name_kor, source_token):
        if alias and alias != display_name and alias not in aliases:
            aliases.append(alias)

    # LLM 제공 name_roman 우선, 없으면 hash fallback
    raw_roman = (stub.get("name_roman") or "").strip().lower()
    raw_roman = re.sub(r'[^a-z0-9_]', '', raw_roman)
    source_roman = _kor_to_roman_id(source_token)
    if _requires_generated_name(reference_kind) and raw_roman == source_roman:
        raw_roman = _kor_to_roman_id(display_name)
    base_id = raw_roman if raw_roman else _kor_to_roman_id(display_name)
    char_id = await _unique_char_id(base_id)
    timestamp = datetime.now().isoformat()

    async with async_driver.session() as session:
        await session.run("""
            CREATE (:Character {id: $id, name: $name, aliases: $aliases, type: "transient"})
        """, id=char_id, name=display_name, aliases=aliases)

        biological_sex = stub.get("biological_sex", "")
        has_menstrual_cycle = _is_female_sex(biological_sex)
        cycle_day = _initial_cycle_day(char_id) if has_menstrual_cycle else None
        age_int = _coerce_int(stub.get("age", ""))

        # StaticProfile is the durable record for transient NPCs. Heavy runtime nodes
        # are reserved for named promotion.
        profile_json = json.dumps({
            "name_kor":              display_name,
            "type":                  "transient",
            "context":               stub.get("context", ""),
            "role":                  stub.get("relation_type", "acquaintance"),
            "source_token":          source_token,
            "reference_kind":        reference_kind,
            "promotion_eligible":    bool(stub.get("promotion_eligible", True)),
            "biological_sex":        biological_sex,
            "age":                   stub.get("age", ""),
            "height":                stub.get("height", ""),
            "weight":                stub.get("weight", ""),
            "measurements":          stub.get("measurements", ""),
            "physique":              stub.get("physique", ""),
            "appearance":            stub.get("appearance", ""),
            "initial_mood":          stub.get("initial_mood", ""),
            "personality":           stub.get("personality", ""),
            "speech_style":          stub.get("speech_style", ""),
            "family":                stub.get("family", ""),
            "formative_background":  stub.get("formative_background", ""),
            "first_seen":            timestamp,
            "last_seen":             timestamp,
            "appearance_count":      0,
            "libido_excluded":       True,
        }, ensure_ascii=False)
        await session.run("""
            MATCH (c:Character {id: $cid})
            CREATE (c)-[:HAS_PROFILE]->(:StaticProfile {
                id: $pid,
                props: $props_json,
                age: $age,
                gender: $gender,
                role: $role
            })
        """,
            cid        = char_id,
            pid        = f"{char_id}_static",
            props_json = profile_json,
            age        = age_int,
            gender     = biological_sex,
            role       = stub.get("relation_type", "acquaintance"),
        )

        # DynamicState remains lightweight turn context. Personality, DynamicInformation,
        # and NeedsState are created only after promotion to named NPC.
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
                cycle_day:          $cycle_day,
                location_id:        "",
                workplace_stress_level: 0,
                outfit:             $outfit,
                injury_marks:       "",
                has_menstrual_cycle: $has_menstrual_cycle,
                pregnant:           false,
                pregnancy_day:      0,
                cum_shots_this_cycle: 0,
                emotional_state:    "",
                physique:           $physique,
                age:                $age
            })
        """,
            cid=char_id,
            state_id=f"{char_id}_state",
            mood=initial_mood,
            cycle_day=cycle_day,
            outfit=stub.get("appearance", ""),
            has_menstrual_cycle=has_menstrual_cycle,
            physique=stub.get("physique", ""),
            age=age_int,
        )

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
