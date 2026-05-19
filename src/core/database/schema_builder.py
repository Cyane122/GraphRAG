# ================================
# src/core/database/schema_builder.py
#
# Kuzu 월드 스키마를 초기화하는 CLI 스크립트입니다.
# 기존 DB 디렉토리를 삭제하고 새로 생성합니다.
#
# Usage:
#   python -m src.core.database.schema_builder --world_id babe_univ
# ================================

import argparse
import shutil
from pathlib import Path

import kuzu

from src.config import WORLD_ID
from src.agents.manager.world_loader import load_world_instance

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--world_id",    type=str, default=WORLD_ID, help="초기화할 세계 ID")
    parser.add_argument("--scenario_id", type=str, default=None,     help="초기화할 시나리오 ID (없으면 기본값)")
    args = parser.parse_args()

    world_id    = args.world_id
    scenario_id = args.scenario_id
    world = load_world_instance(world_id, scenario_id)

    # Kuzu DB 삭제 (파일 단독 형식 또는 디렉토리 형식 모두 처리)
    db_path = Path("graph") / world_id
    for p in [db_path, db_path.with_suffix(".kuzu"), db_path.with_suffix(".wal")]:
        if p.is_dir():
            shutil.rmtree(p)
            print(f"[{world_id}] 기존 DB 삭제 완료 (dir): {p}")
        elif p.is_file():
            p.unlink()
            print(f"[{world_id}] 기존 DB 삭제 완료 (file): {p}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    print(f"현재 World: [{world_id}] / Scenario: [{scenario_id or 'default'}]")
    try:
        world.build_schema(conn, scenario_id)
    finally:
        # Kuzu는 명시적 close 불필요
        pass
