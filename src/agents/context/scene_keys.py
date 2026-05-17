# ================================
# src/agents/context/scene_keys.py
#
# Normalizes scene labels for downstream context lookups.
#
# Functions
#   - normalize_scene_type(scene_type: str | None) -> str : Return a supported context scene key
#   - normalize_scene_types(scene_types: list[str] | None) -> list[str] : Normalize and deduplicate scene keys
# ================================

_SCENE_CONTEXT_ALIASES: dict[str, str] = {
    "aggressive": "tense",
    "vulnerable": "emotional",
    "bonding": "emotional",
    "aegyo": "daily",
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
