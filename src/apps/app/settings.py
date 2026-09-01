# ================================
# src/apps/app/settings.py
#
# 앱 전역 설정의 JSON 영속 저장소 (data/app_settings.json).
# 스레드별 설정(ConversationState)과 달리 모든 채팅방에 공통 적용된다.
#
# Classes
#   - AppSettings : 전역 토글·thinking level 설정
#
# Functions
#   - normalize_thinking_level(value: str | None, default: str) -> str : thinking level 문자열을 LOW/MEDIUM/HIGH로 정규화합니다.
#   - load_settings() -> AppSettings : 전역 설정을 로드합니다 (없으면 기본값).
#   - save_settings(settings: AppSettings) -> AppSettings : 전역 설정을 저장하고 반영값을 반환합니다.
#   - wiki_updater_model_name() -> str : Wiki updater 호출에 쓸 모델 이름을 한 곳에서 해석합니다.
# ================================

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from src.config import MODEL_PRO_UPDATER

_SETTINGS_FILE = Path("data") / "app_settings.json"
_THINKING_LEVELS = {"LOW", "MEDIUM", "HIGH"}
_DEFAULT_ACTOR_THINKING_LEVEL = "HIGH"
_DEFAULT_WIKI_UPDATER_THINKING_LEVEL = "MEDIUM"


class AppSettings(BaseModel):
    """앱 전역 토글과 thinking level 설정. 기본값은 현행 동작을 보존한다."""

    output_repair_enabled: bool = True
    actor_thinking_level: str = _DEFAULT_ACTOR_THINKING_LEVEL
    wiki_updater_thinking_level: str = _DEFAULT_WIKI_UPDATER_THINKING_LEVEL


def normalize_thinking_level(value: str | None, default: str) -> str:
    """Return a canonical thinking level or the provided default."""
    if not isinstance(value, str):
        return default
    candidate = value.strip().upper()
    if candidate in _THINKING_LEVELS:
        return candidate
    return default


def load_settings() -> AppSettings:
    """전역 설정을 읽는다. 파일이 없거나 손상됐으면 기본값을 반환한다(조용한 fallback)."""
    try:
        raw = _SETTINGS_FILE.read_text(encoding="utf-8")
    except OSError:
        return AppSettings()
    try:
        payload = json.loads(raw)
    except ValueError:
        # 손상된 설정 파일은 무시하고 기본값으로 진행한다.
        return AppSettings()
    if not isinstance(payload, dict):
        return AppSettings()
    payload["actor_thinking_level"] = normalize_thinking_level(
        payload.get("actor_thinking_level"),
        _DEFAULT_ACTOR_THINKING_LEVEL,
    )
    payload["wiki_updater_thinking_level"] = normalize_thinking_level(
        payload.get("wiki_updater_thinking_level"),
        _DEFAULT_WIKI_UPDATER_THINKING_LEVEL,
    )
    try:
        return AppSettings.model_validate(payload)
    except ValueError:
        return AppSettings()


def save_settings(settings: AppSettings) -> AppSettings:
    """전역 설정을 디스크에 저장하고 저장된 객체를 반환한다."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(
        settings.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return settings


def wiki_updater_model_name() -> str:
    """Wiki updater 호출(update_accepted_turn)에 쓸 모델 이름을 한 곳에서 해석한다.

    지금은 `src.config.MODEL_PRO_UPDATER` 고정값을 그대로 반환한다. 대화별로
    updater 모델을 고를 수 있게 할지는 아직 미해결 갈래(F4)이고 이 함수는 그
    질문과 무관하게, 나중에 F4가 "가능"으로 결론 나도 참조 지점을 한 곳만
    바꾸면 되도록 세 호출부(wiki_controls/wiki_message_ops/wiki_service)를
    묶어 두는 역할만 한다.
    """
    return MODEL_PRO_UPDATER
