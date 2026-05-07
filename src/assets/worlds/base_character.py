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
#   - _insert_rel(conn, from_id, to_id, rel_type, affinity, trust, status) -> None
#   - _merge_static_event(conn, event_id, name, foreshadow_conditions,
#                         foreshadow_hint, trigger_conditions,
#                         involved_ids, status) -> None
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
    """캐릭터 구현체 베이스 클래스."""

    id: str = ""
    name: str = ""
    aliases: list[str] = []
    char_type: str = "npc"

    def build_schema(self, conn: kuzu.Connection) -> None:
        """캐릭터 노드, StaticProfile, DynamicState를 Kuzu에 삽입합니다."""
        raise NotImplementedError

    def build_relationship(self, conn: kuzu.Connection, other: Character) -> None:
        """self → other 방향 RELATIONSHIP 엣지를 생성합니다. 모르는 상대는 no-op."""
        pass
