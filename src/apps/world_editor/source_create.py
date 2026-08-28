# ================================
# src/apps/world_editor/source_create.py
#
# Compatibility facade for world-editor source creation and registry APIs.
# Implementations live in source_ops.creator.
#
# Functions
#   - add_relationship(world_id: str, source: str, target: str, rel_type: str, affinity: int, trust: int, current_status: str) -> dict : Add a relationship.
#   - delete_relationship(world_id: str, source: str, target: str) -> dict : Delete a relationship.
#   - add_tuple_row(world_id: str, kind: str, values: dict) -> dict : Add a tuple row.
#   - delete_tuple_row(world_id: str, kind: str, row_id: str) -> dict : Delete a tuple row.
#   - add_event(world_id: str, event: dict) -> dict : Add an event.
#   - delete_event(world_id: str, event_id: str) -> dict : Delete an event.
#   - set_blob(world_id: str, char_id: str, role: str, props: dict) -> dict : Upsert a blob.
#   - set_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict : Set character state.
#   - edit_subnode(world_id: str, char_id: str, node_id: str, fields: dict) -> dict : Edit a subnode.
#   - add_subnode(world_id: str, char_id: str, kind: str, fields: dict) -> dict : Add a subnode.
#   - add_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Add a schedule.
#   - set_aliases(world_id: str, char_id: str, aliases: list[str]) -> dict : Set aliases.
#   - register_character(world_id: str, class_name: str, char_id: str, char_type: str) -> dict : Register a character.
#   - list_all_characters(world_id: str) -> list[dict] : List all characters.
#   - get_scenario_characters(world_id: str, scenario_id: str | None) -> list[str] : Get scenario characters.
#   - set_scenario_characters(world_id: str, scenario_id: str | None, char_ids: list[str]) -> dict : Set scenario characters.
#   - create_scenario(world_id: str, scenario_id: str, display_name: str) -> dict : Create a scenario.
#   - update_scenario_meta(world_id: str, scenario_id: str, display_name: str) -> dict : Update scenario metadata.
#   - rename_scenario(world_id: str, old_scenario_id: str, new_scenario_id: str) -> dict : Rename a scenario.
#   - update_scene_types(world_id: str, scene_types: dict[str, str], scenario_id: str | None = None) -> dict : Update scene types.
#   - update_default_perspective(world_id: str, perspective: object, scenario_id: str | None = None) -> dict : Update perspective.
#   - add_extra_slot(world_id: str, slot_id: str, label: str, sub: str) -> dict : Add an extra slot.
#   - delete_extra_slot(world_id: str, slot_id: str) -> dict : Delete an extra slot.
# ================================

from __future__ import annotations

import sys

from src.apps.world_editor.source_ops import creator as _implementation

sys.modules[__name__] = _implementation
