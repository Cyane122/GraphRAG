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
from importlib import import_module
from pathlib import Path

import kuzu

from src.assets.worlds.base import World


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--world_id", type=str, default="default", help="초기화할 세계 ID")
    args = parser.parse_args()

    world_id = args.world_id
    try:
        module = import_module(f"src.assets.worlds.{world_id}.schema")
        world  = module.world_instance
    except (ModuleNotFoundError, AttributeError):
        world = World()

    # Kuzu DB는 디렉토리 형태 — 완전히 삭제 후 재생성
    db_path = Path("graph") / world_id
    if db_path.exists():
        shutil.rmtree(db_path)
        print(f"[{world_id}] 기존 DB 삭제 완료: {db_path}")

    db_path.mkdir(parents=True, exist_ok=True)
    db   = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    print(f"현재 World: [{world_id}]")
    try:
        world.build_schema(conn)
    finally:
        # Kuzu는 명시적 close 불필요
        pass
