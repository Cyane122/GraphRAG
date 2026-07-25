# ================================
# src/simulation/state/apply/__init__.py
#
# 추출된 상태·이벤트·관계를 Kuzu 그래프 DB에 반영하는 적용 레이어.
# 정책(update_policy), 감사(audit), 시간 계획(time_plan)도 포함.
# ================================

from src.simulation.state.apply.events import delegate_complex_update
from src.simulation.state.apply.relationships import apply_scene_relationship_updates
from src.simulation.state.apply.time_plan import apply_time_updates, build_time_plan, commit_time_plan
from src.simulation.state.apply.audit import guard_actor_response
from src.simulation.state.apply.update_policy import (
    has_event_signal,
    should_run_auxiliary_character_updates,
    should_run_life_depth_system,
    should_run_secondary_relationship_updates,
)

__all__ = [
    "delegate_complex_update",
    "apply_scene_relationship_updates",
    "apply_time_updates",
    "build_time_plan",
    "commit_time_plan",
    "guard_actor_response",
    "has_event_signal",
    "should_run_auxiliary_character_updates",
    "should_run_life_depth_system",
    "should_run_secondary_relationship_updates",
]
