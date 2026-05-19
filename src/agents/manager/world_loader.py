# ================================
# src/agents/manager/world_loader.py
#
# Load the World class for the active WORLD_ID.
#
# Functions
#   - load_world_instance(world_id: str, scenario_id: str | None) -> World
# ================================
from importlib import import_module

from src.assets.worlds.base import World

def load_world_instance(world_id: str, scenario_id: str | None = None) -> World:
    try:
        module = import_module(f"src.assets.worlds.{world_id}.schema")

        # 신규 스타일: 모듈 레벨 SCENARIOS: list[Scenario]
        scenarios = getattr(module, "SCENARIOS", None)
        if isinstance(scenarios, list):
            if scenario_id:
                for sc in scenarios:
                    if sc.scenario_id == scenario_id and sc.world is not None:
                        return sc.world
            # scenario_id 없거나 매칭 실패 → 첫 번째 시나리오
            if scenarios and scenarios[0].world is not None:
                return scenarios[0].world

        # 레거시: 모듈에 미리 만들어진 world_instance
        if isinstance(getattr(module, "world_instance", None), World):
            return module.world_instance

        # 최후 수단: 클래스 직접 인스턴스화
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, World) and obj is not World:
                return obj()
    except Exception as e:
        print(f"[WorldLoader] {world_id} 로드 실패: {e}")
    return World()


# ════════════════════════════════════════════════════════════
