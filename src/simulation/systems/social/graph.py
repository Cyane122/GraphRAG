# ================================
# src/simulation/systems/social/graph.py
#
# Compatibility facade for Social-system graph behavior split into identity, profile, persistence, and relationship modules.
# ================================
from src.simulation.systems.social.identity import _cache_key, _coerce_int, _fallback_identity_for_reference, _get_known_chars, _get_primary_names, _initial_cycle_day, _invalidate_cache, _is_female_sex, _normalize_reference_kind, _requires_generated_name, _resolve_identity
from src.simulation.systems.social.relationships import _fetch_character_types, _initial_relationship_for_pair, _unique_ordered, ensure_scene_relationships
from src.simulation.systems.social.stub_persist import _create_stub, _ensure_runtime_nodes_in_session, _increment_appearance, _link_to_event, _unique_char_id, ensure_character_runtime_nodes
from src.simulation.systems.social.stub_profile import _build_conservative_stub_profile, _fetch_static_family_text, _is_stub_candidate, _is_usable_generated_name, _lookup_surname, _normalize_relation_descriptor_for_family, _parse_relation_descriptor, _primary_name_for_id, _sentence_snippets_for_name, _sibling_role_from_family, _snippet_with_markers, _stub_world_context
