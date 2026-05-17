# ================================
# src/agents/context/__init__.py
#
# 동적 프롬프트 컨텍스트 수집 및 렌더링 패키지 공개 인터페이스.
#
# Classes
#   - ContextPlan : 씬별 컨텍스트 범위 및 시스템 선택 스냅샷
#   - SceneState  : 씬 연속성 스냅샷
#
# Functions
#   - build_context_plan(scene_types, user_input, scene_state, world_config) -> ContextPlan : 턴별 컨텍스트 범위 결정
#   - context_plan_to_prompt_dict(plan: ContextPlan) -> dict : 프롬프트 안전 딕셔너리 변환
#   - get_scene_state(...) -> SceneState : 현재 SceneState 로드 또는 생성
#   - update_scene_state_after_response(...) -> SceneState : 커밋 타임 SceneState 업데이트
#   - scene_state_to_prompt_dict(scene_state: SceneState) -> dict : 프롬프트 안전 상태 딕셔너리 변환
#   - build_rendered_dynamic_context(...) -> dict[str, str] : 동적 컨텍스트 블록 렌더링
#   - fetch_generic_prompt_context(npc_id, pc_id, location_id, scene_type) -> dict : 범용 프롬프트 노드 조회
# ================================

from src.agents.context.planner import (
    ContextPlan,
    build_context_plan,
    context_plan_to_prompt_dict,
)
from src.agents.context.scene_state import (
    SceneState,
    get_scene_state,
    scene_state_to_prompt_dict,
    update_scene_state_after_response,
)
from src.agents.context.renderer import build_rendered_dynamic_context
from src.agents.context.generic import fetch_generic_prompt_context
