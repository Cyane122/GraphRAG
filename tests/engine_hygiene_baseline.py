# ================================
# tests/engine_hygiene_baseline.py
#
# Frozen set of unused-import violations tests/smoke_engine_hygiene.py already knew
# about when the baseline ratchet was introduced (2026-09-01), scoped to code that the
# 0.1.1-engine-dedup cycle did not touch. See F5 in
# .re0/iteration/0.1.1-engine-dedup/DESIGN.local.md for why this exists as a tests/
# smoke check instead of a linter, and _check_unused_imports in
# tests/smoke_engine_hygiene.py for how this set is used: it asserts the *current*
# violation set equals this set exactly, in both directions — a violation not listed
# here fails as new, and a listed entry no longer present fails until removed. That
# second rule is the ratchet: this list can only shrink.
#
# DO NOT add an entry here to make a freshly introduced violation pass. That defeats
# the entire point of this file, which exists to stop the bug class F5 describes
# (an unused import nobody notices) from growing back — not to give new violations a
# place to hide. Only remove an entry (when someone actually fixes the unused import)
# or, in a separately reviewed change, add an entry that is provably pre-existing
# debt outside the change's scope — never to paper over something the change itself
# introduced.
#
# Variables
#   - UNUSED_IMPORT_BASELINE : frozenset[str] of "path:line:name" violation keys, one
#     per pre-existing unused import. `path` is POSIX-style and relative to the repo
#     root, matching tests/smoke_engine_hygiene.py's `_unused_import_key`.
# ================================

from __future__ import annotations

UNUSED_IMPORT_BASELINE: frozenset[str] = frozenset({
    "src/apps/world_editor/scaffold.py:19:Path",
    "src/apps/world_editor/source_ops/annotate.py:13:_coerce_state_bool_value",
    "src/apps/world_editor/source_ops/annotate.py:13:_coerce_state_int_value",
    "src/apps/world_editor/source_ops/annotate.py:13:normalize_cfg_state_values",
    "src/apps/world_editor/source_ops/annotate.py:13:normalize_state_fields",
    "src/apps/world_editor/source_ops/core.py:28:normalize_state_fields",
    "src/apps/world_editor/source_ops/core.py:29:_base_indent",
    "src/apps/world_editor/source_ops/core.py:29:_emit",
    "src/apps/world_editor/source_ops/core.py:29:_line_offsets",
    "src/apps/world_editor/source_ops/core.py:29:_literal_eval_segment",
    "src/apps/world_editor/source_ops/core.py:29:_node_span",
    "src/apps/world_editor/source_ops/core.py:29:_replace_node_span",
    "src/apps/world_editor/source_ops/creator_support.py:11:dataclass",
    "src/apps/world_editor/source_ops/locators.py:15:sys",
    "src/apps/world_editor/source_ops/locators.py:18:_base_indent",
    "src/apps/world_editor/source_ops/locators.py:18:_byte_col_to_codepoint",
    "src/apps/world_editor/source_ops/locators.py:18:_line_offsets",
    "src/apps/world_editor/source_ops/locators.py:18:_literal_eval_segment",
    "src/apps/world_editor/source_ops/locators.py:18:_node_span",
    "src/apps/world_editor/source_ops/locators.py:18:_replace_node_span",
    "src/apps/world_editor/source_ops/locators.py:27:world_pkg_dir",
    "src/apps/world_editor/source_text.py:18:_base_indent",
    "src/apps/world_editor/source_text.py:18:_byte_col_to_codepoint",
    "src/apps/world_editor/source_text.py:18:_emit",
    "src/apps/world_editor/source_text.py:18:_line_offsets",
    "src/apps/world_editor/source_text.py:18:_literal_eval_segment",
    "src/apps/world_editor/source_text.py:18:_node_span",
    "src/apps/world_editor/source_text.py:18:_replace_node_span",
    "src/core/database/driver.py:37:KuzuRecord",
    "src/core/database/driver.py:37:KuzuResult",
    "src/core/embedding/encoder.py:22:EMBEDDING_DIM",
    "src/simulation/state/apply/time_plan.py:18:parse_prose_header_text",
    "src/simulation/state/graph_apply.py:29:get_dynamic_state_field_types",
    "src/simulation/state/graph_apply.py:52:delegate_complex_update",
    "src/simulation/state/graph_apply.py:65:apply_time_updates",
    "src/simulation/state/graph_apply.py:65:build_time_plan",
    "src/simulation/state/graph_apply.py:65:commit_time_plan",
    "src/simulation/systems/goals/__init__.py:22:GoalStatus",
    "src/simulation/systems/items/__init__.py:20:ensure_item_memory",
    "src/simulation/systems/items/__init__.py:26:fetch_object_memory_hints",
    "src/simulation/systems/items/__init__.py:26:format_item_memory_hints",
    "src/simulation/systems/items/__init__.py:34:ItemHint",
    "src/simulation/systems/items/__init__.py:34:ItemUpdateResult",
    "src/simulation/systems/items/__init__.py:34:_ItemAction",
    "src/simulation/systems/items/__init__.py:34:_ItemCandidate",
    "src/simulation/systems/memory/__init__.py:42:datetime",
    "src/simulation/systems/needs/math.py:16:NEED_BASE_RATES",
    "src/simulation/systems/social/__init__.py:11:build_world_context",
    "src/simulation/systems/social/__init__.py:11:fetch_sns_panel_state",
    "src/simulation/systems/social/graph.py:6:_cache_key",
    "src/simulation/systems/social/graph.py:6:_coerce_int",
    "src/simulation/systems/social/graph.py:6:_fallback_identity_for_reference",
    "src/simulation/systems/social/graph.py:6:_get_known_chars",
    "src/simulation/systems/social/graph.py:6:_get_primary_names",
    "src/simulation/systems/social/graph.py:6:_initial_cycle_day",
    "src/simulation/systems/social/graph.py:6:_invalidate_cache",
    "src/simulation/systems/social/graph.py:6:_is_female_sex",
    "src/simulation/systems/social/graph.py:6:_normalize_reference_kind",
    "src/simulation/systems/social/graph.py:6:_requires_generated_name",
    "src/simulation/systems/social/graph.py:6:_resolve_identity",
    "src/simulation/systems/social/graph.py:7:_fetch_character_types",
    "src/simulation/systems/social/graph.py:7:_initial_relationship_for_pair",
    "src/simulation/systems/social/graph.py:7:_unique_ordered",
    "src/simulation/systems/social/graph.py:7:ensure_scene_relationships",
    "src/simulation/systems/social/graph.py:8:_create_stub",
    "src/simulation/systems/social/graph.py:8:_ensure_runtime_nodes_in_session",
    "src/simulation/systems/social/graph.py:8:_increment_appearance",
    "src/simulation/systems/social/graph.py:8:_link_to_event",
    "src/simulation/systems/social/graph.py:8:_unique_char_id",
    "src/simulation/systems/social/graph.py:8:ensure_character_runtime_nodes",
    "src/simulation/systems/social/graph.py:9:_build_conservative_stub_profile",
    "src/simulation/systems/social/graph.py:9:_fetch_static_family_text",
    "src/simulation/systems/social/graph.py:9:_is_stub_candidate",
    "src/simulation/systems/social/graph.py:9:_is_usable_generated_name",
    "src/simulation/systems/social/graph.py:9:_lookup_surname",
    "src/simulation/systems/social/graph.py:9:_normalize_relation_descriptor_for_family",
    "src/simulation/systems/social/graph.py:9:_parse_relation_descriptor",
    "src/simulation/systems/social/graph.py:9:_primary_name_for_id",
    "src/simulation/systems/social/graph.py:9:_sentence_snippets_for_name",
    "src/simulation/systems/social/graph.py:9:_sibling_role_from_family",
    "src/simulation/systems/social/graph.py:9:_snippet_with_markers",
    "src/simulation/systems/social/graph.py:9:_stub_world_context",
    "src/simulation/systems/social/identity.py:13:datetime",
    "src/simulation/systems/world_dynamics/organic.py:19:datetime",
})
