# ================================
# src/apps/world_editor/source_edit.py
#
# Compatibility facade for the world-editor source-edit API. Implementations
# live in source_ops.editor.
#
# Functions
#   - find_character_file(world_id: str, char_id: str) -> Path | None : Locate a character source file.
#   - annotate_graph(world_id: str, graph: dict) -> None : Annotate a graph with editability metadata.
#   - merge_cfg_dict(base: dict, override: dict) -> dict : Recursively merge character configuration.
#   - edit_character_cfg(world_id: str, char_id: str, scope: str, scenario_id: str | None, values: dict) -> dict : Edit character configuration.
#   - edit_relationship(world_id: str, source: str, target: str, rel_type: str | None, affinity: int | None, trust: int | None, current_status: str | None) -> dict : Edit a relationship.
#   - edit_blob(world_id: str, char_id: str, role: str, props: dict, _label: str | None = None) -> dict : Edit a profile blob.
#   - edit_state(world_id: str, char_id: str, fields: dict, scenario_id: str | None = None) -> dict : Edit character state.
#   - edit_tuple_row(world_id: str, kind: str, row_id: str, values: dict) -> dict : Edit a tuple row.
#   - edit_schedule(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Edit schedule fields.
#   - rewrite_schedule_call(world_id: str, char_id: str, schedule_id: str, fields: dict) -> dict : Rewrite a schedule call.
# ================================

from __future__ import annotations

import sys

from src.apps.world_editor.source_ops import editor as _implementation

sys.modules[__name__] = _implementation
