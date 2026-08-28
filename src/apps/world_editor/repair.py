# ================================
# src/apps/world_editor/repair.py
#
# Compatibility facade for world-editor repair reporting and application.
# Implementations live in source_ops.repairs.
#
# Functions
#   - build_repair_report(world_id: str, scenario_id: str | None, graph: dict) -> dict : Return repair candidates.
#   - repair_issue(world_id: str, scenario_id: str | None, graph: dict, issue_type: str, scope: str, target: str, apply: bool = False) -> dict : Preview or apply one repair.
# ================================

from __future__ import annotations

import sys

from src.apps.world_editor.source_ops import repairs as _implementation

sys.modules[__name__] = _implementation
