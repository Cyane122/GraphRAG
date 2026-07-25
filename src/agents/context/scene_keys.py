# ================================
# src/agents/context/scene_keys.py
#
# Normalizes scene labels for downstream context lookups.
#
# Functions
#   - normalize_scene_type(scene_type: str | None) -> str : Return a supported context scene key
#   - normalize_scene_types(scene_types: list[str] | None) -> list[str] : Normalize and deduplicate scene keys
#   - normalize_prompt_scene_type(scene_type: str | None) -> str : Return a scene key backed by prompt assets.
#   - normalize_prompt_scene_types(scene_types: list[str] | None) -> list[str] : Normalize and deduplicate prompt scene keys.
# ================================

_SCENE_CONTEXT_ALIASES: dict[str, str] = {
    "aggressive": "tense",
    "vulnerable": "emotional",
    "bonding": "emotional",
    "aegyo": "daily",
}

_SCENE_PROMPT_ALIASES: dict[str, str] = {
    "emotional": "bonding",
    "vulnerable": "bonding",
    "physical": "action",
    "workplace": "formal",
}


def normalize_scene_type(scene_type: str | None) -> str:
    """Return the downstream context key for a classifier scene label."""
    key = str(scene_type or "daily").strip().lower()
    if not key:
        return "daily"
    return _SCENE_CONTEXT_ALIASES.get(key, key)


def normalize_scene_types(scene_types: list[str] | None) -> list[str]:
    """Normalize scene labels and preserve their first-seen order."""
    normalized: list[str] = []
    for scene_type in scene_types or ["daily"]:
        key = normalize_scene_type(scene_type)
        if key not in normalized:
            normalized.append(key)
    return normalized or ["daily"]


def normalize_prompt_scene_type(scene_type: str | None) -> str:
    """Return a normalized key that has a shared scene prompt asset."""
    raw_key = str(scene_type or "daily").strip().lower() or "daily"
    context_key = normalize_scene_type(raw_key)
    return _SCENE_PROMPT_ALIASES.get(
        context_key,
        _SCENE_PROMPT_ALIASES.get(raw_key, context_key),
    )


def normalize_prompt_scene_types(
    scene_types: list[str] | None,
) -> list[str]:
    """Normalize prompt scene keys and preserve first-seen order."""
    normalized: list[str] = []
    for scene_type in scene_types or ["daily"]:
        key = normalize_prompt_scene_type(scene_type)
        if key not in normalized:
            normalized.append(key)
    return normalized or ["daily"]
