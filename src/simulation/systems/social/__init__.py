# ================================
# src/simulation/systems/social/__init__.py
#
# Public API for the Social system and character appearance updates.
#
# Functions
#   - build_world_context(npc_id: str, pc_id: str, location_id: str, current_time: datetime, enable_sns: bool = True) -> dict : Build nearby activity and optional SNS feed context
#   - fetch_sns_panel_state(npc_id: str, pc_id: str, current_time: datetime, limit: int = 12) -> dict : Build UI-ready SNS feed state
#   - resolve_and_update(char_names: list[str], main_npc_id: str, pc_id: str, world_config: dict, event_id: str | None = None, event_importance: int = 0) -> list[str] : Resolve characters and update appearance records
# ================================
from src.simulation.systems.social.context import build_world_context, fetch_sns_panel_state
from src.simulation.systems.social.graph import (
    _create_stub,
    ensure_scene_relationships,
    _get_known_chars,
    _increment_appearance,
    _invalidate_cache,
    _link_to_event,
    _resolve_identity,
)
from src.simulation.systems.social.promotion import check_and_promote

async def resolve_and_update(
    char_names:       list[str],
    main_npc_id:      str,
    pc_id:            str,
    world_config:     dict,
    event_id:         str | None = None,
    event_importance: int = 0,
) -> list[str]:
    """
    CoT에서 파싱한 등장인물 이름 목록을 받아 처리.
    Returns: 이번 턴에 새로 생성된 char_id 목록
    """
    if not char_names:
        return []

    known       = await _get_known_chars()
    resolved_ids: list[str] = []

    for name in char_names:
        char_id = _resolve_identity(name, known)

        if char_id:
            resolved_ids.append(char_id)
            if event_id:
                await _link_to_event(char_id, event_id)
            await _increment_appearance(char_id)
            if await check_and_promote(char_id, main_npc_id, event_importance):
                _invalidate_cache()
        else:
            char_id = await _create_stub(
                name_kor     = name,
                main_npc_id  = main_npc_id,
                pc_id        = pc_id,
                world_config = world_config,
            )
            if char_id:
                resolved_ids.append(char_id)
                _invalidate_cache()
                if event_id:
                    await _link_to_event(char_id, event_id)
                await _increment_appearance(char_id)

    await ensure_scene_relationships([pc_id, main_npc_id, *resolved_ids])
    return resolved_ids
