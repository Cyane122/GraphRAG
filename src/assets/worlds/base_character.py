# ================================
# src/assets/worlds/base_character.py
#
# Character 베이스 클래스. 모든 세계 캐릭터 구현체가 상속하는 인터페이스.
# 관계·이벤트 삽입 공용 헬퍼 함수도 이 파일에 위치한다.
#
# Classes
#   - Character : 캐릭터 베이스. build_schema / build_relationship 인터페이스 정의.
#
# Functions
#   - _insert_rel(conn: kuzu.Connection, from_id: str, to_id: str, rel_type: str, affinity: int, trust: int, status: str) -> None
#   - _merge_dict(base: dict, override: dict) -> dict : scenario config를 재귀 병합합니다.
#   - _merge_static_event(conn: kuzu.Connection, event_id: str, name: str, foreshadow_conditions: str, foreshadow_hint: str, trigger_conditions: str, involved_ids: list[str], status: str = "pending") -> None
# ================================

from __future__ import annotations

import kuzu


def _insert_rel(
    conn: kuzu.Connection,
    from_id: str,
    to_id: str,
    rel_type: str,
    affinity: int,
    trust: int,
    status: str,
) -> None:
    """RELATIONSHIP 엣지를 Kuzu에 삽입합니다."""
    conn.execute(
        "MATCH (a:Character {id: $a}), (b:Character {id: $b}) "
        "CREATE (a)-[:RELATIONSHIP {type: $t, affinity: $af, trust: $tr, current_status: $st}]->(b)",
        {"a": from_id, "b": to_id, "t": rel_type, "af": affinity, "tr": trust, "st": status},
    )


def _merge_dict(base: dict, override: dict) -> dict:
    """base 위에 override를 재귀 병합한 새 dict를 반환합니다."""
    merged = dict(base)
    for key, value in override.items():
        old_value = merged.get(key)
        if isinstance(old_value, dict) and isinstance(value, dict):
            merged[key] = _merge_dict(old_value, value)
        else:
            merged[key] = value
    return merged


def _merge_static_event(
    conn: kuzu.Connection,
    event_id: str,
    name: str,
    foreshadow_conditions: str,
    foreshadow_hint: str,
    trigger_conditions: str,
    involved_ids: list[str],
    status: str = "pending",
) -> None:
    """StaticEvent 노드와 EVENT_INVOLVES 엣지를 MERGE로 생성합니다.

    이미 동일한 event_id가 존재하면 노드·엣지 모두 건너뜁니다.
    build_relationship 안에서 인라인으로 호출하는 것을 의도합니다.
    """
    conn.execute(
        "MERGE (e:StaticEvent {id: $id}) "
        "ON CREATE SET e.name = $name, "
        "e.foreshadow_conditions = $foreshadow_conditions, "
        "e.foreshadow_hint = $foreshadow_hint, "
        "e.trigger_conditions = $trigger_conditions, "
        "e.status = $status",
        {
            "id":                    event_id,
            "name":                  name,
            "foreshadow_conditions": foreshadow_conditions,
            "foreshadow_hint":       foreshadow_hint,
            "trigger_conditions":    trigger_conditions,
            "status":                status,
        },
    )
    for char_id in involved_ids:
        conn.execute(
            "MATCH (e:StaticEvent {id: $event_id}), (c:Character {id: $char_id}) "
            "MERGE (e)-[:EVENT_INVOLVES]->(c)",
            {"event_id": event_id, "char_id": char_id},
        )


class Character:
    """캐릭터 구현체 베이스 클래스.

    시나리오별 분기 패턴
    ────────────────────
    1. 서브클래스에서 DEFAULT_CFG 로 기본값 전체를 정의한다.
    2. SCENARIO_OVERRIDES 에는 달라지는 값만 적는다 (delta).
    3. __init__(scenario_id) 호출 시 두 dict 가 병합돼 self.cfg 에 저장된다.
    4. build_schema / build_relationship 에서 self.cfg 를 읽어 사용한다.

    예시:
        DEFAULT_CFG = {"club": None, "affinity_sian": 5}
        SCENARIO_OVERRIDES = {
            "volleyball_team": {"club": "volleyball", "affinity_sian": 10},
        }
    """

    id: str = ""
    name: str = ""
    aliases: list[str] = []
    char_type: str = "npc"

    DEFAULT_CFG: dict = {}
    SCENARIO_OVERRIDES: dict[str, dict] = {}

    def __init__(self, scenario_id: str | None = None) -> None:
        """scenario_id에 맞는 DEFAULT_CFG와 SCENARIO_OVERRIDES를 병합합니다."""
        self.scenario_id = scenario_id
        self.cfg = _merge_dict(self.DEFAULT_CFG, self.SCENARIO_OVERRIDES.get(scenario_id or "default", {}))

    def build_schema(self, conn: kuzu.Connection) -> None:
        """캐릭터 노드, StaticProfile, DynamicState를 Kuzu에 삽입합니다."""
        raise NotImplementedError

    def build_relationship(self, conn: kuzu.Connection, other: Character) -> None:
        """self → other 방향 RELATIONSHIP 엣지를 생성합니다. 모르는 상대는 no-op."""
        pass
