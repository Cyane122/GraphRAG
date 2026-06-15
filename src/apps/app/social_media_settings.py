# ================================
# src/apps/app/social_media_settings.py
#
# KakaoTalk and Instagram-style side-panel feature flags.
#
# Functions
#   - resolve_social_media_features(world_config: dict | None, overrides: dict | None = None) -> dict : Merge world-forced flags with session overrides.
#   - set_social_media_override(feature: str, enabled: bool, world_config: dict | None, overrides: dict | None = None) -> dict : Update one non-forced feature override.
# ================================

DEFAULT_SOCIAL_MEDIA = {
    "kakao_enabled": False,
    "instagram_enabled": False,
    "force_disable_kakao": True,
    "force_disable_instagram": True,
}


def resolve_social_media_features(
    world_config: dict | None,
    overrides: dict | None = None,
) -> dict:
    """Return UI-ready booleans with KakaoTalk/SNS globally deprecated."""
    raw_config = dict(DEFAULT_SOCIAL_MEDIA)
    raw_config.update((world_config or {}).get("social_media") or {})
    raw_overrides = overrides or {}

    kakao_forced = True
    instagram_forced = True
    kakao_enabled = bool(raw_config.get("kakao_enabled", False))
    instagram_enabled = bool(raw_config.get("instagram_enabled", False))

    if not kakao_forced and "kakao_enabled" in raw_overrides:
        kakao_enabled = bool(raw_overrides["kakao_enabled"])
    if not instagram_forced and "instagram_enabled" in raw_overrides:
        instagram_enabled = bool(raw_overrides["instagram_enabled"])

    if kakao_forced:
        kakao_enabled = False
    if instagram_forced:
        instagram_enabled = False

    return {
        "kakao_enabled": kakao_enabled,
        "instagram_enabled": instagram_enabled,
        "force_disable_kakao": kakao_forced,
        "force_disable_instagram": instagram_forced,
        "any_enabled": kakao_enabled or instagram_enabled,
        "any_forced_disabled": kakao_forced or instagram_forced,
    }


def set_social_media_override(
    feature: str,
    enabled: bool,
    world_config: dict | None,
    overrides: dict | None = None,
) -> dict:
    """Update one feature override unless the active world forces it off."""
    current = dict(overrides or {})
    features = resolve_social_media_features(world_config, current)
    if feature == "kakao" and not features["force_disable_kakao"]:
        current["kakao_enabled"] = bool(enabled)
    if feature == "instagram" and not features["force_disable_instagram"]:
        current["instagram_enabled"] = bool(enabled)
    return current
