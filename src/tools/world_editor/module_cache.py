# ================================
# src/tools/world_editor/module_cache.py
#
# 월드 에디터가 디스크의 최신 월드 소스를 다시 import 하도록 모듈 캐시를 정리합니다.
#
# Functions
#   - purge_world_modules(world_id: str) -> None : 지정 월드의 import/bytecode 캐시를 비웁니다.
# ================================

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import src.assets.worlds as _worlds_pkg


def _world_pkg_dir(world_id: str) -> Path:
    """월드 패키지 디렉터리 경로를 반환합니다."""
    return Path(_worlds_pkg.__path__[0]) / world_id


def purge_world_modules(world_id: str) -> None:
    """월드의 import 캐시와 bytecode 캐시를 비워 다음 import가 디스크 소스를 읽게 합니다."""
    prefix = f"src.assets.worlds.{world_id}"
    for name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[name]
    importlib.invalidate_caches()
    for pyc_dir in _world_pkg_dir(world_id).rglob("__pycache__"):
        shutil.rmtree(pyc_dir, ignore_errors=True)
