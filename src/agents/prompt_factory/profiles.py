# ================================
# src/agents/prompt_factory/profiles.py
#
# Conversation-scoped prose-profile catalog, normalization, and asset rendering.
#
# Classes
#   - ProseProfile : Stable base, primary, and modifier selection for one conversation.
#
# Functions
#   - normalize_prose_profile(value: object, legacy_variant: str | None = None) -> ProseProfile : Normalize a profile, enforce modifier-group exclusivity, and migrate legacy variants.
#   - prose_profile_catalog() -> dict[str, object] : Return the public profile catalog.
#   - render_prose_profile(profile: ProseProfile) -> str : Render the profile's cacheable prompt assets.
#   - effective_prose_profile(profile: ProseProfile, *, adult_engine_enabled: bool) -> ProseProfile : Couple adult-register modifiers to the adult engine at render time, without mutating the stored profile.
# ================================

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "profiles"
_DEFAULT_BASE = "ko_webnovel_v1"
_DEFAULT_PRIMARY = "general_v1"
_ADULT_REGISTER_GROUP = "adult_register"
_DEFAULT_ADULT_MODIFIER = "erotic_commercial"
# Single ordered (id, label, group) table driving support checks, catalog labels,
# and mutual-exclusivity groups. group=None means the modifier stands alone; the
# four erotic_* modifiers share "adult_register" (mutually exclusive, coupled to
# the adult engine at render time).
_MODIFIERS: list[tuple[str, str, str | None]] = [
    ("relaxed_v1", "완화", None),
    ("erotic_commercial", "성인 · 상업지", _ADULT_REGISTER_GROUP),
    ("erotic_hentai", "성인 · 히토미", _ADULT_REGISTER_GROUP),
    ("erotic_adult_comic", "성인 · 성인지", _ADULT_REGISTER_GROUP),
    ("erotic_humiliation", "성인 · 능욕", _ADULT_REGISTER_GROUP),
]
_MODIFIER_GROUPS: dict[str, str | None] = {modifier_id: group for modifier_id, _label, group in _MODIFIERS}
_SUPPORTED_MODIFIERS = frozenset(_MODIFIER_GROUPS)


class ProseProfile(BaseModel):
    """Cacheable prose selection persisted on a conversation and assistant messages."""

    base: str = _DEFAULT_BASE
    primary: str = _DEFAULT_PRIMARY
    modifiers: list[str] = Field(default_factory=list)


def _dedupe_modifiers_by_group(modifiers: list[str]) -> list[str]:
    """Keep at most one modifier per non-null group and drop duplicate ids.

    Within a group (or for a repeated plain id), the LAST occurrence in the input
    list wins, since the UI appends the newest selection last. The relative order
    of the surviving entries is preserved rather than moved to the end.
    """
    last_index_by_key: dict[str, int] = {}
    for index, modifier_id in enumerate(modifiers):
        key = _MODIFIER_GROUPS.get(modifier_id) or modifier_id
        last_index_by_key[key] = index
    keep_indices = set(last_index_by_key.values())
    return [modifier_id for index, modifier_id in enumerate(modifiers) if index in keep_indices]


def normalize_prose_profile(value: object, legacy_variant: str | None = None) -> ProseProfile:
    """Return a supported profile, migrating A/B/C/D selections deterministically.

    Accepts an already-built ProseProfile, a plain dict from JSON or disk, or None.
    A ProseProfile instance is converted with `model_dump()` and falls through the
    same normalization as a dict, so a conversation or message saved before the
    modifier-group invariant existed (e.g. several erotic_* modifiers at once) is
    still normalized on every read rather than returned unchanged. Unsupported
    modifier ids are dropped, then `_dedupe_modifiers_by_group` enforces at most
    one modifier per non-null group (last occurrence wins) and removes duplicate
    ids. `legacy_variant` carries the retired A/B/C/D selection, which stays
    accepted for one release: B and C gain the relaxed modifier, A and D fall back
    to the default.
    """
    if isinstance(value, ProseProfile):
        value = value.model_dump()
    candidate = value if isinstance(value, dict) else {}
    raw_modifiers = candidate.get("modifiers") if isinstance(candidate.get("modifiers"), list) else []
    modifiers = [item for item in raw_modifiers if isinstance(item, str) and item in _SUPPORTED_MODIFIERS]
    modifiers = _dedupe_modifiers_by_group(modifiers)
    if str(legacy_variant or "").strip().lower() in {"b", "c"} and "relaxed_v1" not in modifiers:
        modifiers.append("relaxed_v1")
    return ProseProfile(modifiers=modifiers)


def prose_profile_catalog() -> dict[str, object]:
    """Return profile choices; future primary assets extend this catalog without code paths."""
    return {
        "default": ProseProfile().model_dump(),
        "bases": [{"id": _DEFAULT_BASE, "label": "한국어 웹소설 기반"}],
        "primaries": [{"id": _DEFAULT_PRIMARY, "label": "일반"}],
        "modifiers": [
            {"id": modifier_id, "label": label, "group": group}
            for modifier_id, label, group in _MODIFIERS
        ],
    }


def render_prose_profile(profile: ProseProfile) -> str:
    """Render base, primary, and selected modifier bodies in deterministic order."""
    asset_ids = [f"base/{profile.base}", f"primary/{profile.primary}"]
    asset_ids.extend(f"modifier/{modifier}" for modifier in profile.modifiers)
    bodies: list[str] = []
    for asset_id in asset_ids:
        path = _PROMPT_DIR / f"{asset_id}.md"
        if path.exists():
            bodies.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(body for body in bodies if body)


def effective_prose_profile(profile: ProseProfile, *, adult_engine_enabled: bool) -> ProseProfile:
    """Return a render-only profile coupling adult-register modifiers to the adult engine.

    Does not mutate `profile`, which stays the source of truth persisted on the
    conversation and its messages. When the adult engine is enabled and no
    adult_register modifier is selected, appends the commercial default. When the
    adult engine is disabled, drops every adult_register modifier. Otherwise
    returns the profile unchanged.
    """
    has_adult_modifier = any(_MODIFIER_GROUPS.get(modifier) == _ADULT_REGISTER_GROUP for modifier in profile.modifiers)
    if adult_engine_enabled:
        if has_adult_modifier:
            return profile
        return profile.model_copy(update={"modifiers": [*profile.modifiers, _DEFAULT_ADULT_MODIFIER]})
    if has_adult_modifier:
        return profile.model_copy(
            update={
                "modifiers": [
                    modifier for modifier in profile.modifiers if _MODIFIER_GROUPS.get(modifier) != _ADULT_REGISTER_GROUP
                ]
            }
        )
    return profile
