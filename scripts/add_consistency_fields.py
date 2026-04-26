"""
일회성 DB 마이그레이션.
기존 DynamicState 노드에 outfit / injury_marks 필드가 없으면 초기값을 설정한다.
스키마를 재빌드하지 않아도 됨.

실행: python scripts/add_consistency_fields.py
"""

import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
)

DEFAULTS = {
    # eun_seo — 헬스장 트레이너, 집에서는 편한 실내복
    "eun_seo_state": {
        "outfit":        "알몸",
        "injury_marks":  "없음",
    },
    # sian
    "sian_state": {
        "outfit":        "알몸",
        "injury_marks":  "없음",
    },
}


def run_migration():
    with driver.session() as session:
        # 1) outfit / injury_marks가 없는 DynamicState 전체에 빈 기본값 적용
        session.run("""
            MATCH (d:DynamicState)
            WHERE d.outfit IS NULL
            SET d.outfit = "알 수 없음"
        """)
        session.run("""
            MATCH (d:DynamicState)
            WHERE d.injury_marks IS NULL
            SET d.injury_marks = "없음"
        """)
        print("✅ 전체 DynamicState — outfit / injury_marks 기본값 설정 완료")

        # 2) 캐릭터별 세밀한 초기값 덮어쓰기
        for state_id, fields in DEFAULTS.items():
            set_clause = ", ".join(f"d.{k} = ${k}" for k in fields)
            result = session.run(
                f"MATCH (d:DynamicState {{id: $id}}) SET {set_clause} RETURN d.id AS id",
                id=state_id, **fields,
            )
            row = result.single()
            if row:
                print(f"✅ {state_id} — 초기값 적용: {fields}")
            else:
                print(f"⚠️  {state_id} — 노드를 찾을 수 없음 (스킵)")

    driver.close()
    print("\n마이그레이션 완료.")


if __name__ == "__main__":
    run_migration()