# ================================
# src/simulation/state/updater.py
#
# Accepted Actor turn을 Graph 또는 Wiki mode에 맞게 반영하는 단일 공개 Updater입니다.
#
# Functions
#   - update_accepted_turn(request: GraphTurnUpdateRequest | WikiTurnUpdateRequest) -> TurnUpdateResult : mode에 맞는 저장소 반영을 실행합니다.
# ================================

from __future__ import annotations

from src.simulation.state.models import (
    GraphTurnUpdateRequest,
    TurnUpdateResult,
    WikiTurnUpdateRequest,
)


async def update_accepted_turn(
    request: GraphTurnUpdateRequest | WikiTurnUpdateRequest,
) -> TurnUpdateResult:
    """Accepted turn을 요청 mode의 영속 상태에 반영하고 공통 결과를 반환합니다."""
    if request.mode == "graph":
        from src.simulation.state.graph_apply import apply_graph_actor_response

        ooc_message = await apply_graph_actor_response(
            request.actor_response,
            request.npc_id,
            request.pc_id,
            scene_types=request.scene_types,
            scene_chars=request.scene_chars,
            world_config=request.world_config,
            manager_effects=request.manager_effects,
            history_snapshot=request.history_snapshot,
            recent_snapshot=request.recent_snapshot,
            thread_id=request.thread_id,
            commit_id=request.commit_id,
            user_input=request.user_input,
        )
        return TurnUpdateResult(mode="graph", ooc_message=ooc_message)

    from src.simulation.state.wiki_apply import apply_wiki_actor_response

    return await apply_wiki_actor_response(request)
