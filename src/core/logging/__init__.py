# ================================
# src/core/logging/__init__.py
#
# core.logging 패키지 공개 인터페이스.
#
# Functions
#   - build_prompt_fingerprint(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict] | None) -> dict : 프롬프트 fingerprint 생성
#   - append_prompt_fingerprint_log(record: dict, logs_dir: Path | str) -> None : fingerprint JSONL 로그 저장
#   - format_prompt_fingerprint(record: dict) -> str : fingerprint 콘솔 요약 생성
# ================================

from src.core.logging.conversation_logger import get_log_path, append_turn, parse_log_file
from src.core.logging.prompt_debug import (
    append_prompt_fingerprint_log,
    build_prompt_fingerprint,
    format_prompt_fingerprint,
)

__all__ = [
    "get_log_path",
    "append_turn",
    "parse_log_file",
    "build_prompt_fingerprint",
    "append_prompt_fingerprint_log",
    "format_prompt_fingerprint",
]
