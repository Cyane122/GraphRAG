# ================================
# src/agents/manager/world_loader.py
#
# Load the World class for the active WORLD_ID.
#
# Functions
#   - load_world_instance(world_id: str) -> World : Load the World instance for a world id
# ================================
from importlib import import_module

from src.assets.worlds.base import World

def load_world_instance(world_id: str) -> World:
    try:
        module = import_module(f"src.assets.worlds.{world_id}.schema")
        # 모듈에 미리 만들어진 world_instance가 있으면 그걸 쓴다.
        # RoFanNorthGenderbendWorld처럼 __init__에 필수 인수가 있는 경우를 처리.
        if isinstance(getattr(module, "world_instance", None), World):
            return module.world_instance
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
                return obj()
    except Exception as e:
        print(f"[WorldLoader] {world_id} 로드 실패: {e}")
    return World()


# ════════════════════════════════════════════════════════════
