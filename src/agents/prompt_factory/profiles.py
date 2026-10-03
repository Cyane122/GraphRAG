# ================================
# src/agents/prompt_factory/profiles.py
#
# Conversation-scoped prose-profile catalog, normalization, and asset rendering.
# Primary styles (genre prompts) are auto-discovered from
# prompts/profiles/primary/*.md, so adding a genre is a content-only change.
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

from src.config import ACTOR_PROMPTS_ROOT


_PROMPT_DIR = ACTOR_PROMPTS_ROOT / "profiles"
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
    ("flutter_v1", "설렘", None),
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


def _frontmatter_label(text: str) -> tuple[str | None, str]:
    """Split a leading ``---`` frontmatter block off an asset body and return its label.

    Parses only a flat ``key: value`` block using plain line/string splitting
    (no YAML dependency, no regex). Returns `(label, body)` where `label` is
    `None` when the asset has no frontmatter or no `label` key, and `body` has
    the frontmatter block (if any) removed and stripped. A frontmatter block
    with no closing ``---`` line is treated as absent so no content is lost.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text.strip()
    label: str | None = None
    end_index: int | None = None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            end_index = index
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "label":
            label = value.strip()
    if end_index is None:
        return None, text.strip()
    body = "\n".join(lines[end_index + 1 :]).strip()
    return label, body


def _discover_primaries() -> list[dict[str, str]]:
    """Scan `primary/*.md` for available primary (genre) styles.

    Re-scans the directory on every call rather than caching, so dropping a
    new `primary/<id>.md` file extends the catalog without a server restart
    (the asset set is small, so the filesystem cost is negligible). The file
    stem is the id; the label comes from an optional leading
    ``---\\nlabel: <text>\\n---`` frontmatter block, falling back to the id
    itself when absent. Results are sorted with the default id first, then
    alphabetically by id, so the catalog order is deterministic regardless of
    directory listing order.
    """
    primary_dir = _PROMPT_DIR / "primary"
    entries: list[tuple[str, str]] = []
    if primary_dir.is_dir():
        for path in primary_dir.glob("*.md"):
            primary_id = path.stem
            label, _body = _frontmatter_label(path.read_text(encoding="utf-8-sig"))
            entries.append((primary_id, label or primary_id))
    if not any(primary_id == _DEFAULT_PRIMARY for primary_id, _label in entries):
        entries.append((_DEFAULT_PRIMARY, "일반"))
    entries.sort(key=lambda entry: (entry[0] != _DEFAULT_PRIMARY, entry[0]))
    return [{"id": primary_id, "label": label} for primary_id, label in entries]


def _is_safe_asset_id(asset_id: str) -> bool:
    """Return whether `asset_id` is a bare filename stem with no path separators.

    Used as a defensive backstop in `render_prose_profile`: normal callers
    already go through `normalize_prose_profile`, which restricts `base`,
    `primary`, and `modifiers` to known ids, but this rejects a hand-built
    `ProseProfile` carrying a path-traversal id such as `"../x"`.
    """
    return bool(asset_id) and all(character.isalnum() or character in "_-" for character in asset_id)


def normalize_prose_profile(value: object, legacy_variant: str | None = None) -> ProseProfile:
    """Return a supported profile, migrating A/B/C/D selections deterministically.

    Accepts an already-built ProseProfile, a plain dict from JSON or disk, or None.
    A ProseProfile instance is converted with `model_dump()` and falls through the
    same normalization as a dict, so a conversation or message saved before the
    modifier-group invariant existed (e.g. several erotic_* modifiers at once) is
    still normalized on every read rather than returned unchanged. Unsupported
    modifier ids are dropped, then `_dedupe_modifiers_by_group` enforces at most
    one modifier per non-null group (last occurrence wins) and removes duplicate
    ids. `base` and `primary` are kept only when they match a known id (the
    single hardcoded base, or a discovered `primary/*.md` id); otherwise they
    fall back to the default, which also closes the path-traversal path a
    string like `"../x"` would otherwise take toward `render_prose_profile`.
    `legacy_variant` carries the retired A/B/C/D selection, which stays
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
    base_candidate = candidate.get("base")
    base = base_candidate if base_candidate == _DEFAULT_BASE else _DEFAULT_BASE
    primary_candidate = candidate.get("primary")
    valid_primary_ids = {entry["id"] for entry in _discover_primaries()}
    primary = primary_candidate if primary_candidate in valid_primary_ids else _DEFAULT_PRIMARY
    return ProseProfile(base=base, primary=primary, modifiers=modifiers)


def prose_profile_catalog() -> dict[str, object]:
    """Return profile choices, discovering primaries from `primary/*.md` on disk.

    Bases stay a single hardcoded entry (only one exists today). Primaries are
    discovered fresh on every call via `_discover_primaries`, so an author adds
    a genre by dropping a `primary/<id>.md` file — optionally with a leading
    ``---\\nlabel: <text>\\n---`` frontmatter block — with no code change and
    no restart.
    """
    return {
        "default": ProseProfile().model_dump(),
        "bases": [{"id": _DEFAULT_BASE, "label": "한국어 웹소설 기반"}],
        "primaries": _discover_primaries(),
        "modifiers": [
            {"id": modifier_id, "label": label, "group": group}
            for modifier_id, label, group in _MODIFIERS
        ],
    }


def render_prose_profile(profile: ProseProfile) -> str:
    """Render base, primary, and selected modifier bodies in deterministic order.

    Each asset's optional leading frontmatter block (the catalog-label source
    for primaries) is stripped before concatenation so it never reaches the
    Actor prompt. Asset ids failing `_is_safe_asset_id` are skipped as a
    defensive backstop; see that function's docstring.
    """
    segments = [("base", profile.base), ("primary", profile.primary)]
    segments.extend(("modifier", modifier) for modifier in profile.modifiers)
    bodies: list[str] = []
    for kind, asset_id in segments:
        if not _is_safe_asset_id(asset_id):
            continue
        path = _PROMPT_DIR / kind / f"{asset_id}.md"
        if path.exists():
            _label, body = _frontmatter_label(path.read_text(encoding="utf-8-sig"))
            if body:
                bodies.append(body)
    return "\n\n".join(bodies)


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
