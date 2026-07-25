# ================================
# src/apps/app/settings.py
#
# 앱 전역 설정의 JSON 영속 저장소 (data/app_settings.json).
# 스레드별 설정(ConversationState)과 달리 모든 채팅방에 공통 적용된다.
#
# Classes
#   - AppSettings : 전역 토글 설정 (output_repair_enabled)
#
# Functions
#   - load_settings() -> AppSettings : 전역 설정을 로드합니다 (없으면 기본값).
#   - save_settings(settings: AppSettings) -> AppSettings : 전역 설정을 저장하고 반영값을 반환합니다.
# ================================

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

_SETTINGS_FILE = Path("data") / "app_settings.json"


class AppSettings(BaseModel):
    """앱 전역 토글 설정. 기본값은 현행 동작(전부 켜짐)을 보존한다."""

    output_repair_enabled: bool = True


def load_settings() -> AppSettings:
    """전역 설정을 읽는다. 파일이 없거나 손상됐으면 기본값을 반환한다(조용한 fallback)."""
    try:
        raw = _SETTINGS_FILE.read_text(encoding="utf-8")
    except OSError:
        return AppSettings()
    try:
        return AppSettings.model_validate_json(raw)
    except ValueError:
        # 손상된 설정 파일은 무시하고 기본값으로 진행한다.
        return AppSettings()


def save_settings(settings: AppSettings) -> AppSettings:
    """전역 설정을 디스크에 저장하고 저장된 객체를 반환한다."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(
        settings.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return settings
