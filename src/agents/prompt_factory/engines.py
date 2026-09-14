# ================================
# src/agents/prompt_factory/engines.py
#
# Per-conversation "engine module" slot catalog and Fixed-section rendering.
# Loads src/agents/prompt_factory/prompts/usernotes/chinese_restaurant/catalog.json
# (slots authored there; this module does not rewrite or reformat that file) and the
# Markdown bodies that live beside it. Mirrors the prose-profile pattern in
# src/agents/prompt_factory/profiles.py: a per-conversation selection that is
# normalized once and fed into the cacheable Fixed prompt segment.
#
# Functions
#   - _is_valid_slot(slot: object) -> bool : Return whether a raw catalog slot has correctly typed id/name/description/policy_group/options fields.
#   - _is_valid_option(option: object) -> bool : Return whether a raw catalog option has correctly typed value/label/file/recommended_for fields.
#   - normalize_engine_modules(value: dict | None) -> dict[str, str] : Validate a slot selection, defaulting every unknown or invalid slot to "off", then sync shared policy_group slots.
#   - adult_engine_enabled(engine_modules: dict[str, str]) -> bool : Return whether the "adult" slot is selected to anything other than "off".
#   - build_engine_modules_section(engine_modules: dict[str, str], *, char_name: str = "", user_name: str = "") -> str : Render enabled slot bodies as <engine_module> blocks.
#   - engine_module_catalog() -> dict : Return the engine-module slot catalog payload for the hosted UI.
# ================================

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CATALOG_DIR = Path(__file__).resolve().parent / "prompts" / "usernotes" / "chinese_restaurant"
_CATALOG_PATH = _CATALOG_DIR / "catalog.json"


def _is_valid_slot(slot: object) -> bool:
    """Return whether a raw catalog slot has correctly typed required fields.

    `id`, `name`, and `description` must be `str`; `policy_group` must be `str`
    or `None`; `options` must be a `list`. Option contents are validated
    separately by `_is_valid_option`.
    """
    if not isinstance(slot, dict):
        return False
    if not all(isinstance(slot.get(key), str) for key in ("id", "name", "description")):
        return False
    policy_group = slot.get("policy_group")
    if policy_group is not None and not isinstance(policy_group, str):
        return False
    return isinstance(slot.get("options"), list)


def _is_valid_option(option: object) -> bool:
    """Return whether a raw catalog option has correctly typed required fields.

    `value`, `label`, `file`, and `recommended_for` must all be `str`, matching
    the shape every option carries in the real catalog.json.
    """
    if not isinstance(option, dict):
        return False
    return all(
        isinstance(option.get(key), str)
        for key in ("value", "label", "file", "recommended_for")
    )


def _load_catalog() -> list[dict]:
    """Load and validate the engine-module slot catalog.

    A missing file, invalid JSON, or a slot/option that fails
    `_is_valid_slot`/`_is_valid_option` type validation degrades that entry (or
    the whole catalog) to empty rather than raising, so a broken catalog.json
    never takes down prompt assembly - including at import time, since
    `slot["id"]` and `option["value"]` are used as dict keys below.
    """
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("engine module catalog unavailable at %s: %s", _CATALOG_PATH, exc)
        return []
    if not isinstance(raw, dict):
        return []
    slots = raw.get("slots")
    if not isinstance(slots, list):
        return []

    validated: list[dict] = []
    for slot in slots:
        if not _is_valid_slot(slot):
            continue
        valid_options = [option for option in slot["options"] if _is_valid_option(option)]
        if not valid_options:
            continue
        validated.append({**slot, "options": valid_options})
    return validated


_CATALOG_SLOTS: list[dict] = _load_catalog()

# Per-slot {option_value: option} index, built once at load time. Iterating
# reversed() so the dict comprehension's last-write-wins overwrite keeps the
# FIRST option on a duplicate value, matching the linear-scan lookup
# (next(...) over slot["options"]) this replaces. Works for an empty
# _CATALOG_SLOTS too (missing/invalid catalog.json degrades to {}), unlike a
# module-level for-loop followed by `del` on names it may never bind.
_SLOT_OPTION_INDEX: dict[str, dict[str, dict]] = {
    slot["id"]: {option["value"]: option for option in reversed(slot["options"])}
    for slot in _CATALOG_SLOTS
}


def normalize_engine_modules(value: dict | None) -> dict[str, str]:
    """Return a supported slot->option-value map, defaulting every slot to "off".

    Unknown slot ids and values outside a slot's option set are dropped (falling
    back to "off" for that slot). Slots that share a non-null policy_group (main
    and memory share "impersonation") are then synced: within each group, the
    first slot in catalog order that is not "off" is the leader, and every later
    slot in the same group that is not "off", holds a different value, and offers
    the leader's value among its options is overwritten to the leader's value. A
    slot left "off" is never turned on. This mirrors the client-side
    `applySelection` in graphrag-chat-site/app/notion/components/PromptEngineModules.tsx,
    which keeps the visible state honest while the user clicks; this function is
    the authoritative server-side enforcement.
    """
    normalized: dict[str, str] = {slot["id"]: "off" for slot in _CATALOG_SLOTS}
    if isinstance(value, dict):
        for slot in _CATALOG_SLOTS:
            slot_id = slot["id"]
            candidate = value.get(slot_id)
            if not isinstance(candidate, str):
                continue
            if candidate == "off" or candidate in _SLOT_OPTION_INDEX[slot_id]:
                normalized[slot_id] = candidate

    leader_value_by_group: dict[str, str] = {}
    for slot in _CATALOG_SLOTS:
        group = slot["policy_group"]
        if not group:
            continue
        current_value = normalized[slot["id"]]
        if current_value == "off":
            continue
        leader_value = leader_value_by_group.get(group)
        if leader_value is None:
            leader_value_by_group[group] = current_value
            continue
        if current_value != leader_value and leader_value in _SLOT_OPTION_INDEX[slot["id"]]:
            normalized[slot["id"]] = leader_value
    return normalized


def adult_engine_enabled(engine_modules: dict[str, str]) -> bool:
    """Return whether the "adult" slot is selected to anything other than "off"."""
    return engine_modules.get("adult", "off") != "off"


def build_engine_modules_section(
    engine_modules: dict[str, str],
    *,
    char_name: str = "",
    user_name: str = "",
) -> str:
    """Render one <engine_module name="..."> block per enabled slot, catalog order.

    WARNING: bodies are third-party verbatim prompt text and must NEVER be passed
    through str.format/str.format_map/_SafeFormatDict/format_prompt_vars* - several
    contain brace sequences (e.g. a bare "{}") that raise ValueError under Python's
    format mini-language. The caller must insert this function's return value raw,
    never through the shared formatting helpers either.

    {{user}}/{{char}} (double-brace, verified in these files; {{char}} handled
    defensively) are resolved with plain str.replace only, double-brace first so a
    bare "{user}"/"{char}" is never left half-substituted. A replacement is skipped
    when its name is empty, leaving the token untouched.
    """
    blocks: list[str] = []
    for slot in _CATALOG_SLOTS:
        slot_id = slot["id"]
        selected_value = engine_modules.get(slot_id, "off")
        if selected_value == "off":
            continue
        option = _SLOT_OPTION_INDEX[slot_id].get(selected_value)
        if option is None:
            continue
        body_path = _CATALOG_DIR / option["file"]
        try:
            body = body_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("engine module body unavailable at %s: %s", body_path, exc)
            continue
        if not body:
            continue
        if user_name:
            body = body.replace("{{user}}", user_name).replace("{user}", user_name)
        if char_name:
            body = body.replace("{{char}}", char_name).replace("{char}", char_name)
        blocks.append(f'<engine_module name="{slot_id}">\n{body}\n</engine_module>')
    return "\n\n".join(blocks)


def engine_module_catalog() -> dict:
    """Return the engine-module slot catalog payload for the hosted UI.

    Serializes as {"slots": [...]}, each slot carrying id/name/description/
    policy_group/options, and each option carrying value/label/recommended_for.
    The "file" field (an internal asset path) is intentionally omitted from this
    API payload.
    """
    return {
        "slots": [
            {
                "id": slot["id"],
                "name": slot["name"],
                "description": slot["description"],
                "policy_group": slot["policy_group"],
                "options": [
                    {
                        "value": option["value"],
                        "label": option["label"],
                        "recommended_for": option["recommended_for"],
                    }
                    for option in slot["options"]
                ],
            }
            for slot in _CATALOG_SLOTS
        ]
    }
