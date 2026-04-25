"""
Actor 응답 → expression_classifier → DB 업데이트 → complex_updater 위임.
"""

import re

from src.utils.db_utils import update_dynamic_state, update_relationship_affinity
from src.updater.expression_classifier import classify_and_extract

# 상태 변화가 일어났을 가능성이 있는 키워드 패턴
_CHANGE_PATTERN = re.compile(
    r"다쳤|부상|병원|골절|삐었|쓰러|기절|아프|열이|입원|"
    r"이동했|나갔|도착|들어왔|장소|"
    r"스트레스|화났|슬퍼|불안|우울|힘들|짜증|무너|"
    r"싸웠|화해|고백|사귀|헤어|"
    r"injured|hospitalized|arrived|moved|stressed"
)

# 이 씬 타입은 키워드 여부 상관없이 항상 분류
_ALWAYS_CLASSIFY = {"intimate", "workplace", "physical"}

COMPLEX_TRIGGERS = {"hospitalized", "affinity"}


def _needs_classification(actor_response: str, scene_types: list[str]) -> bool:
    """Haiku 호출이 필요한지 판단. False면 분류 전체 스킵."""
    if any(t in scene_types for t in _ALWAYS_CLASSIFY):
        return True
    return bool(_CHANGE_PATTERN.search(actor_response))


async def process_actor_response(
    actor_response: str,
    npc_id:         str,
    pc_id:          str,
    scene_types:    list[str] | None = None,
    scene_chars:    list[str] | None = None,
    world_config:   dict | None = None,
) -> dict:
    """
    Actor 응답을 분석하여 상태 업데이트.
    scene_types 미전달 시 항상 분류.
    scene_chars: CoT에서 파싱한 등장인물 풀네임 목록 → world_builder로 전달.
    """
    if scene_types and not _needs_classification(actor_response, scene_types):
        print("[StateUpdater] 스킵 (변화 키워드 없음)")
        # world_builder는 상태 변화 없어도 실행
        if world_config and scene_chars:
            from src.world.world_builder import resolve_and_update
            await resolve_and_update(scene_chars, npc_id, pc_id, world_config)
        return {"updated": {}, "delegated_to_complex": False}

    changes = classify_and_extract(actor_response)
    if not changes:
        if world_config and scene_chars:
            from src.world.world_builder import resolve_and_update
            await resolve_and_update(scene_chars, npc_id, pc_id, world_config)
        return {"updated": {}, "delegated_to_complex": False}

    physical_val  = changes.get("physical_condition", "")
    needs_complex = (
        physical_val == "hospitalized"
        or "affinity" in changes
        or ("injury_detail" in changes and "physical_condition" in changes)
    )

    simple_changes = {
        k: v for k, v in changes.items()
        if k not in {"affinity"} and not needs_complex
    }
    if simple_changes:
        await update_dynamic_state(npc_id, simple_changes)

    if "affinity" in changes and not needs_complex:
        delta = changes["affinity"]
        if isinstance(delta, (int, float)):
            await update_relationship_affinity(npc_id, pc_id, int(delta))

    if needs_complex:
        from src.updater.complex_updater import delegate_complex_update
        await delegate_complex_update(
            actor_response  = actor_response,
            npc_id          = npc_id,
            pc_id           = pc_id,
            initial_changes = changes,
            world_config    = world_config,
            scene_chars     = scene_chars or [],
        )

    return {"updated": simple_changes, "delegated_to_complex": needs_complex}