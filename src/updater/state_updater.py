# src/updater/state_updater.py
"""
Actor 응답을 받은 뒤 비동기로 실행되는 단순 상태 업데이터.

흐름:
  Actor 응답 → expression_classifier.classify_and_extract()
             → DB 업데이트 (neo4j)
             → 복합 이벤트 판별 → Sonnet 위임 (complex_updater)

단순 업데이트 조건:
  - 단일 노드 변경 (DynamicState만)
  - 수치/위치/감정 변경

복합 이벤트 위임 조건:
  - physical_condition + injury_detail 동시 변경
  - affinity 변경 (관계 노드도 함께 업데이트 필요)
  - hospitalized (입원 이벤트 생성 필요)
"""

import asyncio
from neo4j import AsyncGraphDatabase
from dotenv import load_dotenv
from pathlib import Path
import os

from src.updater.expression_classifier import classify_and_extract

load_dotenv(Path(__file__).parent.parent.parent / ".env")

async_driver = AsyncGraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

# 복합 업데이트가 필요한 필드 조합
COMPLEX_TRIGGERS = {
    "hospitalized",   # physical_condition = hospitalized
    "affinity",       # 관계 노드 변경 필요
}


# ════════════════════════════════════════════════════════════
# 단순 DB 업데이트
# ════════════════════════════════════════════════════════════

async def _update_dynamic_state(char_id: str, updates: dict) -> None:
    """DynamicState 노드 속성 비동기 업데이트."""
    if not updates:
        return
    set_clause = ", ".join(f"d.{k} = ${k}" for k in updates)
    async with async_driver.session() as session:
        await session.run(f"""
            MATCH (c:Character {{id: $char_id}})-[:HAS_STATE]->(d:DynamicState)
            SET {set_clause}
        """, char_id=char_id, **updates)
    print(f"[StateUpdater] {char_id} → {updates}")


async def _update_relationship(char_a: str, char_b: str, affinity_delta: int) -> None:
    """affinity 변화 → RELATIONSHIP 엣지 업데이트."""
    async with async_driver.session() as session:
        await session.run("""
            MATCH (a:Character {id: $a})-[r:RELATIONSHIP]->(b:Character {id: $b})
            SET r.affinity = CASE
                WHEN r.affinity + $delta > 100 THEN 100
                WHEN r.affinity + $delta < -100 THEN -100
                ELSE r.affinity + $delta
            END
        """, a=char_a, b=char_b, delta=affinity_delta)
    print(f"[StateUpdater] relationship {char_a}→{char_b} affinity Δ{affinity_delta:+d}")


# ════════════════════════════════════════════════════════════
# 메인 진입점 (비동기)
# ════════════════════════════════════════════════════════════

async def process_actor_response(
    actor_response: str,
    npc_id: str,
    pc_id: str,
) -> dict:
    """
    Actor 응답을 비동기로 분석하여 상태 업데이트.
    복합 이벤트는 complex_updater에 위임.

    Returns:
        {"updated": dict, "delegated_to_complex": bool}
    """
    # 1. 표현 분류 + 필드 추출
    changes = classify_and_extract(actor_response)
    if not changes:
        return {"updated": {}, "delegated_to_complex": False}

    # 2. 복합 이벤트 판별
    needs_complex = False
    physical_val  = changes.get("physical_condition", "")

    if physical_val == "hospitalized":
        needs_complex = True
    if "affinity" in changes:
        needs_complex = True
    if "injury_detail" in changes and "physical_condition" in changes:
        needs_complex = True   # 부상 복합 처리

    # 3. 단순 업데이트 분리 처리
    simple_changes = {
        k: v for k, v in changes.items()
        if k not in {"affinity"} and not needs_complex
    }

    if simple_changes:
        await _update_dynamic_state(npc_id, simple_changes)

    # 4. affinity는 별도 처리 (단순이라도)
    if "affinity" in changes and not needs_complex:
        delta = changes["affinity"]
        if isinstance(delta, (int, float)):
            await _update_relationship(npc_id, pc_id, int(delta))

    # 5. 복합 이벤트 위임 (import는 순환 방지를 위해 지연)
    if needs_complex:
        from src.updater.complex_updater import delegate_complex_update
        await delegate_complex_update(actor_response, npc_id, pc_id, changes)

    return {
        "updated":               simple_changes,
        "delegated_to_complex":  needs_complex,
    }


# ── 동기 래퍼 (Chainlit에서 asyncio.create_task로 호출 시 사용) ─
def schedule_update(actor_response: str, npc_id: str, pc_id: str) -> None:
    """
    Chainlit의 이벤트 루프에서 fire-and-forget으로 호출.
    응답 생성과 병렬 실행되므로 유저 응답을 블로킹하지 않음.
    """
    asyncio.create_task(
        process_actor_response(actor_response, npc_id, pc_id)
    )