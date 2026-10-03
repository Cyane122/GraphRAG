# ================================
# src/core/logging/__init__.py
#
# core.logging 패키지 공개 인터페이스.
#
# Classes
#   - AcceptedTurn : 확정 턴 1건의 accepted-turn 로그 레코드
#   - ConversationLogError : accepted-turn 로그 읽기·쓰기 실패 예외
#
# Functions
#   - append_accepted_turn(turn: AcceptedTurn) -> bool : 같은 event_id가 없을 때만 확정 턴을 기록
#   - read_accepted_turns(path: Path) -> list[AcceptedTurn] : accepted-turn JSONL을 검증하며 읽기
#   - accepted_turns_path(thread_id: str) -> Path : thread의 accepted-turn JSONL 경로
#   - build_prompt_fingerprint(fixed_prompt: str, dynamic_prompt: str, history: list[dict] | None) -> dict : 프롬프트 fingerprint 생성
#   - append_prompt_fingerprint_log(record: dict, logs_dir: Path | str) -> None : fingerprint JSONL 로그 저장
#   - format_prompt_fingerprint(record: dict) -> str : fingerprint 콘솔 요약 생성
# ================================

from src.core.logging.conversation_logger import (
    AcceptedTurn,
    ConversationLogError,
    accepted_turns_path,
    append_accepted_turn,
    append_turn,
    get_log_path,
    parse_log_file,
    read_accepted_turns,
)
from src.core.logging.prompt_debug import (
    append_prompt_fingerprint_log,
    build_prompt_fingerprint,
    format_prompt_fingerprint,
)

__all__ = [
    "AcceptedTurn",
    "ConversationLogError",
    "accepted_turns_path",
    "append_accepted_turn",
    "read_accepted_turns",
    "get_log_path",
    "append_turn",
    "parse_log_file",
    "build_prompt_fingerprint",
    "append_prompt_fingerprint_log",
    "format_prompt_fingerprint",
]
