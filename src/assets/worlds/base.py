# ================================
# src/assets/worlds/base.py
#
# World 베이스 클래스. 모든 세계 구현체가 상속하는 인터페이스.
# world_section / specific_prose_rules / few_shot_examples /
# blacklist / npc_name_map / start_time 및 _build_tables / build_schema 정의.
#
# Classes
#   - Scenario : 한 세계 내의 시작 설정 (시간, 장소, 오프닝 씬 경로, 씬 타입 override).
#   - World : 세계 구현체 베이스 클래스
#             SCENARIOS: dict[str, Scenario] — 시나리오 ID → Scenario. 비어있으면 단일 세계.
#             SCENE_TYPES: dict[str, str] — 씬 타입 이름 → 영문 설명 (classifier 프롬프트에 주입)
#             EXTRA_SLOTS: list — 커스텀 캐릭터 슬롯 [{id, label, sub}]. world_editor 로 관리.
#             DYNAMIC_SLOT_UPDATERS: list[dict] — accepted response 후 갱신할 커스텀 슬롯 설정.
#             get_scene_types() -> list[str]           : 타입 이름 목록 (내부 키 조회용)
#             get_scene_descriptions() -> dict[str, str] : 전체 dict (classifier 주입용)
#             resolve_pov() -> tuple[str, bool]        : perspective 설정(int/2·3-튜플) → (pov_mode, impersonation) 정규화
#             get_default_perspective() -> int         : 세계 기본 인칭(1/3) 반환
#             get_dynamic_slot_updaters() -> list[dict] : 커스텀 슬롯 후처리 설정 반환
#             FIELD_TYPES: dict[str, dict[str, str]]     — 필드 타입 분류 (appearance/personality/other). world_editor가 field_types.json에 저장.
#             get_field_types() -> dict[str, dict[str, str]] : field_types.json + FIELD_TYPES 병합 반환
#             get_social_media_config() -> dict          : 카카오톡/SNS 기능 기본값과 월드 강제 비활성화 설정
#             _build_tables(conn) -> None              : DDL 전용 (노드·관계 테이블, 벡터 인덱스, GlobalState)
#             build_schema(conn, scenario_id) -> None  : 기본 구현은 _build_tables + build_scenario_data 호출
#             build_scenario_data(conn, scenario_id) -> None : 시나리오별 초기 데이터 훅 (no-op)
#
# Functions
#   - apply_scenario_overrides(world: World, scenario: Scenario | object) -> World : Scenario override를 World 인스턴스에 적용합니다.
#   - insert_static(conn: kuzu.Connection, label: str, node_id: str, *, char_id: str | None = None, rel: str | None = None, **props: object) -> None : JSON blob 노드를 생성하고 선택적으로 Character에 연결합니다.
#   - insert_static_inline(conn: kuzu.Connection, char_id: str, rel: str, label: str, node_id: str, **props: object) -> None : Character → JSON blob 노드 관계를 생성합니다.
#   - insert_dynamic(conn: kuzu.Connection, char_id: str, node_id: str | None = None, **props: object) -> None : DynamicInformation 노드를 생성하고 Character에 연결합니다.
#   - insert_state(conn: kuzu.Connection, char_id: str, node_id: str | None = None, **props: object) -> None : DynamicState 노드를 생성하고 Character에 연결합니다.
#   - insert_rule(conn: kuzu.Connection, rule_id: str, **props) -> None : Rule 노드를 생성합니다.
#   - insert_schedule(conn: kuzu.Connection, owner_id: str, schedule_id: str, **props) -> None : Schedule 노드를 생성하고 Character에 연결합니다.
#   - apply_schedule_templates(conn: kuzu.Connection, world_id: str, scenario_id: str | None) -> None : 월드 schedule_templates.json을 적용합니다.
# ================================

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import kuzu

from src.core.embedding.encoder import EMBEDDING_DIM

if TYPE_CHECKING:
    from src.assets.worlds.base_character import Character


# DynamicState 의 '기본(built-in)' 컬럼 — DDL(_build_tables)에 박혀 있고 UI 가 기본 필드로 노출.
# 이 목록에 없는 커스텀 state 컬럼은 빌드 시 insert_state 가 ALTER TABLE 로 만들어 함께 빌드한다
# (즉 이 frozenset 은 '드롭 필터'가 아니라 '기본값 정의'다).
_DYNAMIC_STATE_COLUMNS: frozenset[str] = frozenset({
    "physical_condition", "mental_condition", "stress_level", "mood", "cycle_day",
    "location_id", "workplace_stress_level", "knee_condition", "injury_detail",
    "energy", "stress", "current_task", "outfit", "injury_marks",
    "has_menstrual_cycle", "pregnant", "pregnancy_day", "cum_shots_this_cycle",
    "pregnancy_father_id", "body_perception", "behavioral_facade", "hygiene",
    "appearance", "physique", "age_presentation", "nervousness", "attitude",
    "social_skill", "consideration", "stamina", "odor", "emotional_state",
    "attachment_risk", "expectation_gap", "penis_size", "age", "circle_level",
    "robe_grade", "led_color",
})


# ── 시나리오 ───────────────────────────────────────────────────────

@dataclass
class Scenario:
    """한 세계 내의 시작 설정.

    새 스타일: world 필드에 설정된 World 인스턴스를 넣으면
    module-level SCENARIOS 리스트로 등록 → ChatProfile 드롭다운에 노출.

    구 스타일 (rofan 등 레거시): world=None 두고 default_time 등 직접 지정.
    """
    scenario_id: str
    display_name: str
    world: "World | None" = field(default=None)          # 신규: 설정된 World 인스턴스
    default_time: datetime | None = None                  # 레거시: world 없을 때만 사용
    default_location_id: str | None = None                # 레거시: world 없을 때만 사용
    opening_scene_path: str = "opening_scene.md"          # 레거시
    scene_types: dict[str, str] | None = None             # 신규: 시나리오별 씬 타입 override


def apply_scenario_overrides(world: "World", scenario: Scenario | object) -> "World":
    """Scenario에 정의된 런타임 override를 복사된 World 인스턴스에 적용하고 반환합니다."""
    resolved = copy(world)
    scene_types = getattr(scenario, "scene_types", None)
    if isinstance(scene_types, dict) and scene_types:
        resolved.SCENE_TYPES = dict(scene_types)
    return resolved


# ── 정적 노드 헬퍼 ─────────────────────────────────────────────────
# StaticProfile / Personality / IntimateProfile / WorkplaceProfile /
# DialogueExamples 는 세계별로 속성 구조가 달라 JSON blob으로 저장합니다.
# 에이전트는 node.props 를 JSON 파싱해 사용합니다.

def _ensure_cypher_identifier(value: str, kind: str) -> None:
    """Kuzu label/relationship 이름으로 쓸 수 있는 단순 식별자인지 검사합니다."""
    if not value.isidentifier():
        raise ValueError(f"{kind} must be a simple identifier: {value!r}")


def insert_static(
    conn: kuzu.Connection,
    label: str,
    node_id: str,
    *,
    char_id: str | None = None,
    rel: str | None = None,
    **props: object,
) -> None:
    """JSON blob 노드를 삽입하고, char_id가 있으면 Character에 연결합니다."""
    _ensure_cypher_identifier(label, "label")
    if char_id is None:
        if rel is not None:
            raise ValueError("rel requires char_id")
        conn.execute(
            f"CREATE (:{label} {{id: $id, props: $props}})",
            {"id": node_id, "props": json.dumps(props, ensure_ascii=False)},
        )
        return

    if rel is None:
        raise ValueError("char_id requires rel")
    _ensure_cypher_identifier(rel, "rel")
    conn.execute(
        f"""
        MATCH (c:Character {{id: $char_id}})
        CREATE (c)-[:{rel}]->(n:{label} {{id: $node_id, props: $props}})
        """,
        {
            "char_id": char_id,
            "node_id": node_id,
            "props": json.dumps(props, ensure_ascii=False),
        },
    )


def insert_static_inline(
    conn: kuzu.Connection,
    char_id: str,
    rel: str,
    label: str,
    node_id: str,
    **props: object,
) -> None:
    """Character → 정적 노드 관계를 한 번에 생성합니다."""
    insert_static(conn, label, node_id, char_id=char_id, rel=rel, **props)


def insert_dynamic(
    conn: kuzu.Connection,
    char_id: str,
    node_id: str | None = None,
    **props: object,
) -> None:
    """DynamicInformation JSON blob 노드를 생성하고 Character에 연결합니다."""
    insert_static(
        conn,
        "DynamicInformation",
        node_id or f"{char_id}_info",
        char_id=char_id,
        rel="HAS_INFO",
        **props,
    )


_KUZU_SCALAR_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "BOOLEAN"),   # bool 은 int 의 subclass 이므로 반드시 int 보다 먼저 검사.
    (int, "INT64"),
    (float, "DOUBLE"),
    (str, "STRING"),
)


def _kuzu_scalar_type(value: object) -> str | None:
    """파이썬 스칼라 값에서 Kuzu 컬럼 타입을 추론합니다 (추론 불가 시 None)."""
    for py_type, kuzu_type in _KUZU_SCALAR_TYPES:
        if isinstance(value, py_type):
            return kuzu_type
    return None


def _dynamic_state_columns(conn: kuzu.Connection) -> set[str]:
    """현재 DynamicState 테이블의 실제 컬럼 이름 집합을 조회합니다 (기본 + 그동안 추가된 커스텀)."""
    res = conn.execute("CALL TABLE_INFO('DynamicState') RETURN name")
    names: set[str] = set()
    while res.has_next():
        names.add(res.get_next()[0])
    return names


def insert_state(
    conn: kuzu.Connection,
    char_id: str,
    node_id: str | None = None,
    **props: object,
) -> None:
    """DynamicState 노드를 생성하고 Character에 연결합니다.

    기본 컬럼(_DYNAMIC_STATE_COLUMNS, DDL 에 박힌 것)은 그대로 쓰고, props 에 그 외
    커스텀 키가 있으면 빌드 시 ALTER TABLE 로 컬럼을 만들어 함께 저장합니다
    (화이트리스트로 드롭하지 않음 — 세계별 커스텀 state 컬럼을 지원).
    None 값이거나 타입을 추론할 수 없는/식별자가 아닌 키는 건너뜁니다.
    """
    state_id = node_id or str(props.get("id") or f"{char_id}_state")
    # id(PK)·None 값은 기록 대상에서 제외.
    cols = {key: value for key, value in props.items() if key != "id" and value is not None}
    if not cols:
        return  # 빈 state 는 노드를 만들지 않는다(기존 동작 보존).

    # 테이블에 없는 커스텀 컬럼은 타입을 추론해 ALTER TABLE 로 추가한다(빌드 시 같이 빌드).
    existing = _dynamic_state_columns(conn)
    for key, value in cols.items():
        if key in existing or not key.isidentifier():
            continue
        kuzu_type = _kuzu_scalar_type(value)
        if kuzu_type is None:
            continue
        conn.execute(f"ALTER TABLE DynamicState ADD {key} {kuzu_type}")
        existing.add(key)

    # existing 에 든 컬럼만 기록(추가 실패/식별자 아님 키는 자연히 제외).
    writable = {key: value for key, value in cols.items() if key in existing}
    payload = {"id": state_id, **writable}
    columns = ", ".join(f"{key}: ${key}" for key in payload)
    conn.execute(f"CREATE (:DynamicState {{{columns}}})", payload)
    conn.execute(
        "MATCH (c:Character {id: $id}), (d:DynamicState {id: $did}) CREATE (c)-[:HAS_STATE]->(d)",
        {"id": char_id, "did": state_id},
    )


def insert_rule(conn: kuzu.Connection, rule_id: str, **props) -> None:
    """Create a generic Rule node used by prompt and time-rule systems."""
    values = {
        "id": rule_id,
        "name": "",
        "summary": "",
        "prompt_hint": "",
        "prompt_priority": 0,
        "tags": [],
        "location_id": "",
        "owner_id": "",
        "scene_type": "",
        "status": "active",
    }
    values.update(props)
    conn.execute(
        """
        CREATE (:Rule {
            id: $id,
            name: $name,
            summary: $summary,
            prompt_hint: $prompt_hint,
            prompt_priority: $prompt_priority,
            tags: $tags,
            location_id: $location_id,
            owner_id: $owner_id,
            scene_type: $scene_type,
            status: $status
        })
        """,
        values,
    )


def insert_schedule(conn: kuzu.Connection, owner_id: str, schedule_id: str, **props) -> None:
    """Create a Schedule node and attach it to the owning Character."""
    values = {
        "id": schedule_id,
        "owner_id": owner_id,
        "name": "",
        "activity": "",
        "summary": "",
        "prompt_hint": "",
        "prompt_priority": 0,
        "material": "",
        "recurrence": "weekly",
        "day_of_week": -1,
        "day_of_weeks": [],
        "date": "",
        "start_time": "",
        "end_time": "",
        "start_minute": -1,
        "end_minute": -1,
        "location_id": "",
        "status": "active",
        "tags": [],
    }
    values.update(props)
    day_values = _normalize_weekdays(values.get("day_of_weeks") or values.get("day_of_week"))
    values["day_of_weeks"] = day_values
    values["day_of_week"] = day_values[0] if day_values else -1
    values["start_minute"] = _normalize_minute(values.get("start_minute"), values.get("start_time"))
    values["end_minute"] = _normalize_minute(values.get("end_minute"), values.get("end_time"))
    create_values = {
        key: values[key]
        for key in (
            "id",
            "owner_id",
            "name",
            "activity",
            "summary",
            "prompt_hint",
            "prompt_priority",
            "material",
            "recurrence",
            "day_of_week",
            "date",
            "start_time",
            "end_time",
            "start_minute",
            "end_minute",
            "location_id",
            "status",
        )
    }
    conn.execute(
        """
        MATCH (c:Character {id: $owner_id})
        CREATE (c)-[:HAS_SCHEDULE]->(:Schedule {
            id: $id,
            owner_id: $owner_id,
            name: $name,
            activity: $activity,
            summary: $summary,
            prompt_hint: $prompt_hint,
            prompt_priority: $prompt_priority,
            material: $material,
            recurrence: $recurrence,
            day_of_week: $day_of_week,
            day_of_weeks: $day_of_weeks,
            date: $date,
            start_time: $start_time,
            end_time: $end_time,
            start_minute: $start_minute,
            end_minute: $end_minute,
            location_id: $location_id,
            status: $status
        })
        """,
        create_values,
    )
    conn.execute(
        """
        MATCH (s:Schedule {id: $id})
        SET s.day_of_weeks = $day_of_weeks,
            s.tags = $tags
        """,
        {
            "id": values["id"],
            "day_of_weeks": values["day_of_weeks"],
            "tags": values["tags"],
        },
    )
    if values["location_id"]:
        conn.execute(
            """
            MATCH (s:Schedule {id: $id}), (l:Location {id: $location_id})
            CREATE (s)-[:SCHEDULED_AT]->(l)
            """,
            {
                "id": values["id"],
                "location_id": values["location_id"],
            },
        )


def apply_schedule_templates(conn: kuzu.Connection, world_id: str, scenario_id: str | None) -> None:
    """world_editor 의 schedule_templates.json 을 Schedule 노드로 런타임에 삽입합니다.

    전역('world') 항목 + 현재 시나리오('scenarios'[scenario_id]) 항목을 합쳐, 각 entry 의
    owner_id 캐릭터에 insert_schedule 로 부착합니다. owner_id/id 가 없거나 해당 Character 가
    아직 없으면(insert_schedule 의 MATCH 가 비면) 조용히 건너뜁니다.
    build_schema 가 캐릭터를 모두 만든 직후에 호출해야 합니다.
    """
    path = Path(__file__).parent / world_id / "schedule_templates.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    entries = list(data.get("world") or [])
    if scenario_id:
        entries += list((data.get("scenarios") or {}).get(scenario_id) or [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        owner_id = entry.get("owner_id")
        schedule_id = entry.get("id")
        if not owner_id or not schedule_id:
            continue  # owner/id 없는 항목은 Schedule 로 부착할 수 없다.
        props = {k: v for k, v in entry.items() if k not in ("owner_id", "id")}
        insert_schedule(conn, owner_id=owner_id, schedule_id=schedule_id, **props)


def _normalize_weekdays(raw: object) -> list[int]:
    """Normalize one weekday or many weekdays into sorted Python weekday numbers."""
    if raw in (None, "", -1):
        return []
    if isinstance(raw, int):
        return [raw] if 0 <= raw <= 6 else []
    if isinstance(raw, (set, tuple, list)):
        days: set[int] = set()
        for value in raw:
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6:
                days.add(day)
        return sorted(days)
    try:
        day = int(raw)
    except (TypeError, ValueError):
        return []
    return [day] if 0 <= day <= 6 else []


def _normalize_minute(raw_minute: object, raw_time: object) -> int:
    """Use an explicit minute value or derive it from an HH:MM string."""
    try:
        minute = int(raw_minute)
    except (TypeError, ValueError):
        minute = -1
    if minute >= 0:
        return minute
    text = str(raw_time or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return -1
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return -1
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return -1
    return hour * 60 + minute


class World:
    WORLD_ID = "default"
    DEFAULT_PERSPECTIVE = 3

    # 비어 있으면 이 세계는 단일 ChatProfile로 노출됩니다.
    # 항목이 있으면 각 Scenario가 별도 ChatProfile ('{world_id}/{scenario_id}')로 노출됩니다.
    SCENARIOS: dict[str, Scenario] = {}

    # 세계별 커스텀 캐릭터 슬롯. world_editor 에서 추가/삭제합니다.
    # 각 항목: {"id": "magic", "label": "Magic", "sub": "마법 능력치"}
    # label 은 Kuzu 노드 테이블명(identifier). _build_tables 에서 DDL 을 자동 생성합니다.
    EXTRA_SLOTS: list = []
    DYNAMIC_SLOT_UPDATERS: list[dict] = []

    # 필드 타입 분류: section → {field_key → "appearance"|"personality"|"other"}
    # world_editor 가 field_types.json 에 저장하는 값이 우선 적용됩니다.
    # 서브클래스에서 직접 하드코딩할 수도 있습니다.
    FIELD_TYPES: dict[str, dict[str, str]] = {}
    SOCIAL_MEDIA: dict[str, bool] = {
        "kakao_enabled": False,
        "instagram_enabled": False,
        "force_disable_kakao": True,
        "force_disable_instagram": True,
    }

    def __init__(
        self,
        narrator: Character | None = None,
        pc: Character | None = None,
        chars: list[Character] | None = None,
        perspective: object | None = None,
        scenario_id: str | None = None,
    ) -> None:
        """세계 인스턴스를 초기화합니다.

        narrator: 서술자 캐릭터 (NPC POV). 미지정 시 get_npc_id 기본값 사용.
        pc: 플레이어 캐릭터. 미지정 시 get_pc_id 기본값 사용.
        chars: 이 세계에 존재하는 캐릭터 목록. build_schema 시 사용.
        perspective: world_editor perspective 설정. int 또는 (인칭, 중심, 사칭) 튜플을 받습니다.
        scenario_id: 이 인스턴스가 담당하는 시나리오 ID.
        """
        self.narrator = narrator
        self.pc = pc
        self.chars: list[Character] = chars or []
        self.perspective_setting = perspective if perspective is not None else self.DEFAULT_PERSPECTIVE
        self.perspective = self._perspective_person(self.perspective_setting)
        self.scenario_id = scenario_id

    # classifier LLM에 주입할 씬 타입 정의. name → description(English).
    # 설명은 classifier 프롬프트에 직접 주입되므로 LLM이 타입을 정확히 구분할 수 있다.
    SCENE_TYPES: dict[str, str] = {
        "daily": "Everyday life with no significant conflict (meals, casual movement, small talk)",
        "bonding": "Emotional intimacy between characters; warmth, vulnerability, closeness — regardless of relationship type",
        "intimate": "Sexual or erotically charged scene; physical closeness with or without emotional connection",
        "formal": "Meetings, negotiations, or situations requiring decorum and social performance",
        "tense": "Unspoken hostility or dread; conflict has not yet surfaced but the air is wrong",
        "conflict": "Active verbal or psychological confrontation: arguments, threats, power struggles",
        "vulnerable": "A character's weakness is exposed — emotional breakdown, exhaustion, confession",
        "action": "Combat, chase, training, or any scene driven by bodily exertion and kinetic energy",
        "ambient": "Environment or mood takes center stage; setting description, sensory immersion",
    }

    def get_scene_types(self) -> list[str]:
        """씬 타입 이름 목록만 반환합니다 (SCENE_REL_MAP 조회·퓨샷 키 매핑 등 내부 용도)."""
        return list(self.SCENE_TYPES.keys())

    def get_scene_descriptions(self) -> dict[str, str]:
        """씬 타입 이름 → 설명 매핑을 반환합니다 (classifier 프롬프트 주입용)."""
        return self.SCENE_TYPES

    def get_default_time(self) -> datetime:
        """기본 시작 시각을 반환합니다."""
        return datetime.now()

    def _perspective_person(self, perspective: object | None = None) -> int:
        """perspective 설정에서 런타임 인칭 정수를 반환합니다."""
        value = self.DEFAULT_PERSPECTIVE if perspective is None else perspective
        if isinstance(value, (tuple, list)):
            return int(value[0]) if value else 3
        return int(value)

    def resolve_pov(self) -> tuple[str, bool]:
        """perspective 설정을 (pov_mode, impersonation) 으로 정규화합니다 (단일 소스).

        backward-compatible 입력 형식:
          - int             : 인칭만. anchor='char', impersonation=False.
          - (person, anchor): impersonation=False.
          - (person, anchor, impersonation): 그대로.
        anchor: 'char'(캐릭터 중심) | 'user'(PC 중심). 'pc' 는 'user' 로 본다.
        제약: 1인칭 + PC 중심(user) 이면 impersonation 을 True 로 강제한다.
        반환 pov_mode 는 '{person}p_{anchor}' (예: '3p_char', '1p_user').
        """
        # 생성자는 self.perspective 를 기존 호출부 호환용 int 로 유지하므로,
        # anchor/impersonation 정보는 별도 원본 설정에서 읽는다.
        dp = self.perspective_setting
        if isinstance(dp, (tuple, list)):
            person = int(dp[0]) if len(dp) >= 1 else 3
            anchor = str(dp[1]) if len(dp) >= 2 else "char"
            impersonation = bool(dp[2]) if len(dp) >= 3 else False
        else:
            person, anchor, impersonation = int(dp), "char", False
        anchor = "user" if str(anchor).lower() in ("user", "pc") else "char"
        if person == 1 and anchor == "user":
            impersonation = True
        return f"{person}p_{anchor}", impersonation

    def get_default_perspective(self) -> int:
        """세계 기본 인칭(1/3)을 반환합니다."""
        pov_mode, _ = self.resolve_pov()
        return int(pov_mode[0])

    def get_world_section(self) -> str:
        """세계관 설명 XML 섹션을 반환합니다."""
        return """<world>
# TITLE

## SUBTITLE
</world>"""

    def get_specific_prose_rules(self, perspective: int = 3) -> str:
        """시점별 작법 규칙 XML 섹션을 반환합니다."""
        return """<character_specific_prose>
# PROSE ARCHITECTURE

## Scene Structure
</character_specific_prose>"""

    def get_few_shot_examples(self, perspective: int = 3) -> dict:
        """씬 타입별 퓨샷 예시를 반환합니다."""
        return {
            "daily": {"good": [], "bad": []},
        }

    def get_social_media_config(self) -> dict:
        """카카오톡/SNS 기능 기본값과 월드 강제 비활성화 설정을 반환합니다."""
        return dict(self.SOCIAL_MEDIA)

    def get_dynamic_slot_updaters(self) -> list[dict]:
        """accepted response 후 갱신할 커스텀 슬롯 설정을 반환합니다."""
        return [dict(item) for item in (self.DYNAMIC_SLOT_UPDATERS or []) if isinstance(item, dict)]

    def get_blacklist(self) -> str:
        """블랙리스트 항목 문자열을 반환합니다."""
        return ""

    def get_field_types(self) -> dict[str, dict[str, str]]:
        """필드 타입 분류 dict를 반환합니다.

        우선순위: field_types.json (world_editor 저장) > FIELD_TYPES 클래스 속성
        반환 형식: {section: {field_key: "appearance"|"personality"|"other"}}
        """
        # 클래스 속성을 기본값으로 사용
        merged: dict[str, dict[str, str]] = {
            section: dict(fields) for section, fields in self.FIELD_TYPES.items()
        }
        # JSON 파일이 있으면 덮어씁니다
        json_path = Path(__file__).parent / self.WORLD_ID / "field_types.json"
        if json_path.exists():
            try:
                with open(json_path, encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict):
                    for section, fields in stored.items():
                        if isinstance(fields, dict):
                            merged.setdefault(section, {}).update(fields)
            except Exception:
                pass
        return merged

    def get_full_config(self, perspective: object | None = None, scenario_id: str | None = None) -> dict:
        """프롬프트 조립에 필요한 전체 설정 딕셔너리를 반환합니다."""
        # 레거시 dict SCENARIOS 지원 (rofan 등)
        _sid = scenario_id or self.scenario_id
        scenario = self.SCENARIOS.get(_sid) if _sid and isinstance(self.SCENARIOS, dict) and self.SCENARIOS else None
        start_time       = (scenario.default_time        if scenario and scenario.default_time        else self.get_default_time())
        default_location = (scenario.default_location_id if scenario and scenario.default_location_id else self.get_default_location_id())
        perspective_setting = self.perspective_setting if perspective is None else perspective
        resolved_perspective = self._perspective_person(perspective_setting)
        return {
            "world_section":        self.get_world_section(),
            "specific_prose_rules": self.get_specific_prose_rules(resolved_perspective),
            "prose_rules":          self.get_specific_prose_rules(resolved_perspective),
            "few_shot_examples":    self.get_few_shot_examples(resolved_perspective),
            "additional_blacklist": self.get_blacklist(),
            "start_time":           start_time,
            "perspective":          resolved_perspective,
            "pc_id":                self.get_pc_id(),
            "npc_id":               self.get_npc_id(),
            "npc_name_kor":         self.npc_name_kor(),
            "default_location_id":  default_location,
            "scenario_id":          _sid,
            "scene_descriptions":   self.get_scene_descriptions(),
            "social_media":         self.get_social_media_config(),
            "dynamic_slot_updaters": self.get_dynamic_slot_updaters(),
            "impersonation":        self.resolve_pov()[1],
            "field_types":          self.get_field_types(),
        }

    def get_default_location_id(self) -> str:
        """기본 위치 ID를 반환합니다."""
        return "default_location"

    def get_npc_name_map(self) -> dict[str, str]:
        """NPC ID → 한국어 이름 매핑을 반환합니다."""
        return {"이름": "Name"}

    def get_pc_id(self) -> str:
        """PC 노드 ID를 반환합니다. narrator/pc가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.pc.id if self.pc else "player"

    def get_npc_id(self) -> str:
        """NPC(서술자) 노드 ID를 반환합니다. narrator가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.narrator.id if self.narrator else "npc"

    def npc_name_kor(self) -> str:
        """NPC(서술자) 한국어 이름을 반환합니다. narrator가 주입된 경우 인스턴스 변수를 우선합니다."""
        return self.narrator.name if self.narrator else "엔피씨"

    def iter_scenario_characters(self, scenario_id: str | None = None) -> list["Character"]:
        """시나리오에 속한 캐릭터 목록을 반환합니다. 현재는 self.chars 전체 반환."""
        return self.chars

    def _build_world_events(self, conn: kuzu.Connection) -> None:
        """세계 레벨 StaticEvent를 생성합니다. 캐릭터 무관 이벤트(계절 전환, 대규모 사건 등).

        서브클래스에서 필요 시 오버라이드합니다. 기본 구현은 no-op.
        """

    def build_scenario_data(self, conn: kuzu.Connection, scenario_id: str | None) -> None:
        """시나리오별 초기 데이터를 삽입합니다. 기본 구현은 no-op.

        GlobalState 업데이트, 캐릭터 초기 위치 설정 등 시나리오 고유 초기화를 여기서 처리합니다.
        """

    def build_schema(self, conn: kuzu.Connection, scenario_id: str | None = None) -> None:
        """DDL + 시나리오 초기 데이터를 실행합니다."""
        self._build_tables(conn)
        self.build_scenario_data(conn, scenario_id)

    def _build_tables(self, conn: kuzu.Connection) -> None:
        """
        Kuzu 스키마를 초기화합니다.

        1. 노드 테이블 생성 (모든 세계 공통)
        2. 관계 테이블 생성
        3. 벡터 인덱스 생성
        4. GlobalState 노드 생성
        """
        dim = int(EMBEDDING_DIM or 1024)

        # ── 노드 테이블 ────────────────────────────────────────
        node_tables = [
            # 자주 쿼리되는 노드: 명시적 타입 컬럼
            "CREATE NODE TABLE IF NOT EXISTS Character(id STRING, name STRING, aliases STRING[], type STRING, PRIMARY KEY(id))",

            # DynamicState: helpers.py에서 직접 SET — 세계별 컬럼 차이를 모두 포괄
            """CREATE NODE TABLE IF NOT EXISTS DynamicState(
                id STRING,
                physical_condition STRING, mental_condition STRING,
                stress_level INT64, mood STRING, cycle_day INT64,
                location_id STRING, workplace_stress_level INT64,
                knee_condition STRING, injury_detail STRING,
                energy DOUBLE, stress DOUBLE, current_task STRING,
                outfit STRING, injury_marks STRING,
                has_menstrual_cycle BOOLEAN,
                pregnant BOOLEAN, pregnancy_day INT64, cum_shots_this_cycle INT64,
                pregnancy_father_id STRING,
                body_perception STRING, behavioral_facade STRING,
                hygiene DOUBLE, appearance DOUBLE, physique STRING,
                age_presentation STRING, nervousness DOUBLE, attitude STRING,
                social_skill DOUBLE, consideration DOUBLE, stamina DOUBLE,
                odor STRING, emotional_state STRING, attachment_risk DOUBLE,
                expectation_gap DOUBLE, penis_size STRING,
                age INT64, circle_level INT64, robe_grade STRING,
                led_color STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Location(
                id STRING,
                name STRING,
                description STRING,
                atmosphere STRING,
                district STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Rule(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                location_id STRING,
                owner_id STRING,
                scene_type STRING,
                status STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS SpeechProfile(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                char_id STRING,
                audience_id STRING,
                scene_type STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS RelationshipProfile(
                id STRING,
                name STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                tags STRING[],
                source_id STRING,
                target_id STRING,
                scene_type STRING,
                PRIMARY KEY(id)
            )""",

            "CREATE NODE TABLE IF NOT EXISTS GlobalState(id STRING, currentLocationId STRING, currentTime STRING, weather STRING, schedule_slot STRING, clients_done INT64, clients_total INT64, flags STRING, today_schedule STRING, schedule_date STRING, PRIMARY KEY(id))",

            f"""CREATE NODE TABLE IF NOT EXISTS Event(
                id STRING, summary STRING, timestamp STRING,
                location_id STRING, impact STRING,
                need_name STRING,
                importance INT64, decay_rate DOUBLE, summary_level INT64,
                memory_type STRING,
                narrative_summary STRING,
                state_summary STRING,
                content STRING,
                status STRING,
                turn_count INT64,
                safety_impact DOUBLE, safety_resolved BOOLEAN, safety_decay_rate DOUBLE,
                embedding FLOAT[{dim}],
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS NeedsState(
                id STRING,
                hunger DOUBLE, rest DOUBLE, social DOUBLE,
                fun DOUBLE, safety DOUBLE, libido DOUBLE,
                PRIMARY KEY(id)
            )""",

            f"""CREATE NODE TABLE IF NOT EXISTS Memory(
                id STRING,
                event_id STRING,
                char_id STRING,
                summary STRING,
                embedding FLOAT[{dim}],
                memory_type STRING,
                narrative_summary STRING,
                state_summary STRING,
                importance INT64,
                distortion_level DOUBLE,
                summary_level INT64,
                created_at STRING,
                last_decayed_at STRING,
                PRIMARY KEY(id)
            )""",

            # 정적 프로파일 노드: 세계별로 속성 구조가 달라 JSON blob으로 저장
            "CREATE NODE TABLE IF NOT EXISTS StaticProfile(id STRING, props STRING, age INT64, gender STRING, role STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS DynamicInformation(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS Personality(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS IntimateProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS WorkplaceProfile(id STRING, props STRING, PRIMARY KEY(id))",
            "CREATE NODE TABLE IF NOT EXISTS DialogueExamples(id STRING, props STRING, PRIMARY KEY(id))",
            """CREATE NODE TABLE IF NOT EXISTS Item(
                id STRING,
                name STRING,
                description STRING,
                owner_id STRING,
                location_id STRING,
                emotional_weight INT64,
                visibility STRING,
                last_seen_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Goal(
                id STRING,
                owner_id STRING,
                title STRING,
                description STRING,
                status STRING,
                progress INT64,
                subtlety INT64,
                next_hint STRING,
                trigger_conditions STRING,
                completion_conditions STRING,
                last_progressed_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS Secret(
                id STRING,
                owner_id STRING,
                title STRING,
                private_summary STRING,
                public_hint STRING,
                status STRING,
                sensitivity INT64,
                reveal_conditions STRING,
                current_reveal_level INT64,
                last_hinted_at STRING,
                PRIMARY KEY(id)
            )""",

            # Schedule: recurring or one-off character routines rendered into dynamic context.
            """CREATE NODE TABLE IF NOT EXISTS Schedule(
                id STRING,
                owner_id STRING,
                name STRING,
                activity STRING,
                summary STRING,
                prompt_hint STRING,
                prompt_priority INT64,
                material STRING,
                recurrence STRING,
                day_of_week INT64,
                day_of_weeks INT64[],
                date STRING,
                start_time STRING,
                end_time STRING,
                start_minute INT64,
                end_minute INT64,
                location_id STRING,
                status STRING,
                tags STRING[],
                PRIMARY KEY(id)
            )""",

            # StaticEvent: 조건 기반 이벤트. foreshadow → trigger 두 단계 조건으로 복선과 발화를 분리
            """CREATE NODE TABLE IF NOT EXISTS StaticEvent(
                id STRING,
                name STRING,
                foreshadow_conditions STRING,
                foreshadow_hint STRING,
                trigger_conditions STRING,
                status STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS PersonalFact(
                id STRING,
                subject_id STRING,
                audience_id STRING,
                category STRING,
                fact_text STRING,
                normalized_key STRING,
                status STRING,
                valid_from STRING,
                valid_until STRING,
                confidence DOUBLE,
                source STRING,
                created_at STRING,
                updated_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS KakaoRoom(
                id STRING,
                name STRING,
                topic STRING,
                status STRING,
                created_at STRING,
                last_active_at STRING,
                PRIMARY KEY(id)
            )""",

            """CREATE NODE TABLE IF NOT EXISTS KakaoMessage(
                id STRING,
                room_id STRING,
                sender_id STRING,
                sender_name STRING,
                content STRING,
                timestamp STRING,
                source STRING,
                status STRING,
                PRIMARY KEY(id)
            )""",
        ]
        for ddl in node_tables:
            conn.execute(ddl)

        # ── 관계 테이블 ────────────────────────────────────────
        rel_tables = [
            "CREATE REL TABLE IF NOT EXISTS HAS_PROFILE(FROM Character TO StaticProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_INFO(FROM Character TO DynamicInformation)",
            "CREATE REL TABLE IF NOT EXISTS HAS_PERSONALITY(FROM Character TO Personality)",
            "CREATE REL TABLE IF NOT EXISTS HAS_STATE(FROM Character TO DynamicState)",
            "CREATE REL TABLE IF NOT EXISTS HAS_INTIMATE(FROM Character TO IntimateProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_WORKPLACE(FROM Character TO WorkplaceProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_DIALOGUE_EXAMPLES(FROM Character TO DialogueExamples)",
            "CREATE REL TABLE IF NOT EXISTS LOCATED_AT(FROM Character TO Location)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SPEECH_PROFILE(FROM Character TO SpeechProfile)",
            "CREATE REL TABLE IF NOT EXISTS HAS_RELATIONSHIP_PROFILE(FROM Character TO RelationshipProfile)",
            "CREATE REL TABLE IF NOT EXISTS PROFILE_TARGET(FROM RelationshipProfile TO Character)",
            "CREATE REL TABLE IF NOT EXISTS APPLIES_AT(FROM Rule TO Location)",
            "CREATE REL TABLE IF NOT EXISTS RULE_FOR_CHARACTER(FROM Rule TO Character)",
            """CREATE REL TABLE IF NOT EXISTS RELATIONSHIP(
                FROM Character TO Character,
                type STRING, affinity INT64, trust INT64,
                duration STRING, origin STRING, current_status STRING,
                summary STRING,
                eun_seo_desire STRING, shared_events STRING[], last_interaction STRING
            )""",
            "CREATE REL TABLE IF NOT EXISTS INVOLVED_IN(FROM Character TO Event)",
            "CREATE REL TABLE IF NOT EXISTS OCCURRED_AT(FROM Event TO Location)",
            "CREATE REL TABLE IF NOT EXISTS REMEMBERS(FROM Character TO Memory)",
            "CREATE REL TABLE IF NOT EXISTS OF_EVENT(FROM Memory TO Event)",
            "CREATE REL TABLE IF NOT EXISTS HAS_NEEDS(FROM Character TO NeedsState)",
            "CREATE REL TABLE IF NOT EXISTS EVENT_INVOLVES(FROM StaticEvent TO Character)",
            "CREATE REL TABLE IF NOT EXISTS PURSUES(FROM Character TO Goal)",
            "CREATE REL TABLE IF NOT EXISTS GOAL_RELATED_EVENT(FROM Goal TO Event)",
            "CREATE REL TABLE IF NOT EXISTS OWNS(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS GAVE(FROM Character TO Item)",
            "CREATE REL TABLE IF NOT EXISTS ANCHORS_MEMORY(FROM Item TO Memory)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SECRET(FROM Character TO Secret)",
            "CREATE REL TABLE IF NOT EXISTS ROOTED_IN(FROM Secret TO Event)",
            "CREATE REL TABLE IF NOT EXISTS TRIGGERED_BY(FROM Secret TO Item)",
            "CREATE REL TABLE IF NOT EXISTS HAS_SCHEDULE(FROM Character TO Schedule)",
            "CREATE REL TABLE IF NOT EXISTS SCHEDULED_AT(FROM Schedule TO Location)",
            "CREATE REL TABLE IF NOT EXISTS PART_OF(FROM Location TO Location)",
            "CREATE REL TABLE IF NOT EXISTS KNOWS_FACT(FROM Character TO PersonalFact)",
            "CREATE REL TABLE IF NOT EXISTS MEMBER_OF(FROM Character TO KakaoRoom)",
            "CREATE REL TABLE IF NOT EXISTS ROOM_HAS_MESSAGE(FROM KakaoRoom TO KakaoMessage)",
            "CREATE REL TABLE IF NOT EXISTS SENT_KAKAO(FROM Character TO KakaoMessage)",
        ]
        for ddl in rel_tables:
            conn.execute(ddl)

        # 커스텀 슬롯 DDL — EXTRA_SLOTS 에 정의된 blob 테이블과 엣지를 생성한다.
        for slot in (self.EXTRA_SLOTS or []):
            lbl = slot.get("label", "") if isinstance(slot, dict) else ""
            if not lbl or not lbl.isidentifier():
                continue
            rel_name = f"HAS_{lbl.upper()}"
            conn.execute(f"CREATE NODE TABLE IF NOT EXISTS {lbl}(id STRING, props STRING, PRIMARY KEY(id))")
            conn.execute(f"CREATE REL TABLE IF NOT EXISTS {rel_name}(FROM Character TO {lbl})")

        print(f"[{self.WORLD_ID}] 노드/관계 테이블 생성 완료.")

        # ── 벡터 인덱스 (Kuzu v0.7+) ──────────────────────────
        try:
            conn.execute("CALL CREATE_VECTOR_INDEX('Event', 'event_embeddings', 'embedding')")
            conn.execute("CALL CREATE_VECTOR_INDEX('Memory', 'memory_embeddings', 'embedding')")
            print(f"[{self.WORLD_ID}] 벡터 인덱스 생성 완료 (dim={dim}).")
        except Exception as e:
            print(f"[{self.WORLD_ID}] 벡터 인덱스 생성 스킵: {e}")

        # ── GlobalState ────────────────────────────────────────
        conn.execute("""
            CREATE (:GlobalState {
                id: 'singleton',
                currentLocationId: $loc,
                currentTime: $time,
                weather: 'Clear',
                flags: '{}'
            })
        """, {"loc": self.get_default_location_id(), "time": self.get_default_time().isoformat()})
        print(f"[{self.WORLD_ID}] GlobalState 생성 완료.")
