# ================================
# src/core/logging/conversation_logger.py
#
# 확정(accepted)된 대화 턴을 thread별 JSONL로 기록하고, 과거 날짜별 Markdown 로그를 읽는 유틸리티입니다.
#
# 신규 형식 (logs/accepted_turns/<thread_id>.jsonl):
#   한 줄에 AcceptedTurn JSON 하나. 본문은 JSON 문자열로 escape되므로 헤더·[U]·---가 섞인
#   임의 텍스트도 그대로 round-trip된다. event_id(mode·thread·commit·메시지 ID·본문 revision)가
#   같은 레코드는 재시도해도 한 번만 기록된다. 기록 시각은 식별자에 포함하지 않는다.
#
# 레거시 형식 (logs/YYYY-MM-DD.md, 읽기 호환만 유지하며 기존 파일은 재작성하지 않는다):
#   ---
#   [U]
#   유저 입력 (멀티라인 허용)
#
#   **YYYY년 M월 D일 요일 HH시 MM분, 장소**
#   AI 응답 본문...
#
# 레거시 파서는 writer가 쓰는 레코드 구분자("\n---\n\n[U]\n")로 레코드를 나누므로 본문 속 ---는
# 레코드를 끊지 않는다. 사용자 입력과 AI 본문의 경계는 writer가 넣은 빈 줄이며, 레코드 안의
# 빈 줄이 정확히 하나일 때만 확정된다. 빈 줄이 여러 개인 레코드(여러 문단 응답 포함)는 날짜
# 헤더 위치로 추정하지 않고 빈 결과 대신 ConversationLogError로 알린다.
# 호출자가 legacy_date_header=True로 명시하면 그런 레코드만 역사적 관례(AI 응답은 빈 줄 뒤
# **YYYY년 / **제국력 YYYY년 헤더로 시작)로 나누고, 행마다 경계 근거를 표시하며 UserWarning을
# 낸다. 사용자 입력에 헤더형 줄이 있으면 잘못 나뉠 수 있으므로 정확한 소유 구분을 보장하지 않는다.
#
# accepted-turn 읽기·중복 검사·추가는 프로세스 내 잠금 하나로 직렬화한다(앱은 단일 프로세스).
#
# Classes
#   - ConversationLogError : accepted-turn 로그를 읽거나 쓸 수 없을 때 발생하는 예외
#   - AcceptedTurn : 확정 턴 1건 (mode, world_id, scenario_id, thread_id, commit_id, 메시지 ID, 본문, accepted_at, revision, event_id)
#
# Functions
#   - get_log_path(dt: datetime | None) -> Path : 레거시 날짜별 Markdown 로그 경로 반환
#   - append_turn(user_input: str, ai_response: str, timestamp: datetime | None) -> None : 레거시 Markdown 형식으로 1턴 추가
#   - parse_log_file(path: Path, legacy_date_header: bool = False) -> list[dict] : 레거시 Markdown 로그를 본문 손실 없이 파싱하고 경계가 모호하면 예외. 명시 opt-in 시 날짜 헤더 관례로 나누고 경고
#   - accepted_turns_path(thread_id: str) -> Path : thread의 accepted-turn JSONL 경로 반환
#   - read_accepted_turns(path: Path) -> list[AcceptedTurn] : accepted-turn JSONL을 검증하며 읽기
#   - append_accepted_turn(turn: AcceptedTurn) -> bool : 같은 event_id가 없을 때만 추가하고 추가 여부 반환
# ================================

import hashlib
import json
import os
import re
import threading
import warnings
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, computed_field

from src.config import LOGS_ROOT

LOGS_DIR = LOGS_ROOT
_LEGACY_RECORD_SEPARATOR = "\n---\n\n[U]\n"
# 역사적 reader(2026-04~05 src/utils, src/core/logging)가 AI 응답 시작으로 삼은 날짜 헤더 패턴.
_LEGACY_AI_HEADER_RE = re.compile(r"\*\*(?:제국력\s+)?\d{4}년")
# ponytail: 프로세스 내 잠금이다. 앱은 단일 프로세스로 실행되므로 충분하며, 여러 프로세스가
# 같은 thread 로그를 쓰는 배포는 지원하지 않는다 — 그때는 OS 파일 잠금으로 바꾼다.
_ACCEPTED_LOG_LOCK = threading.Lock()


class ConversationLogError(RuntimeError):
    """accepted-turn 로그를 읽거나 쓸 수 없을 때 발생합니다."""


def _sha256_json(value: list[str | None]) -> str:
    """값 목록을 구분자 충돌 없는 JSON으로 직렬화한 SHA-256을 반환합니다."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AcceptedTurn(BaseModel):
    """확정된 사용자/Actor 턴 1건입니다. event_id는 기록 시각과 무관한 안정 식별자입니다."""

    mode: Literal["graph", "wiki"]
    world_id: str
    scenario_id: str | None = None
    thread_id: str = Field(min_length=1)
    commit_id: str = Field(min_length=1)
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    user_input: str
    ai_response: str
    accepted_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def revision(self) -> str:
        """확정된 본문 쌍의 내용 revision입니다. 편집된 확정본은 다른 값을 가집니다."""
        return _sha256_json([self.user_input, self.ai_response])

    @computed_field
    @property
    def event_id(self) -> str:
        """같은 확정 이벤트의 재시도를 하나로 묶는 식별자입니다."""
        return _sha256_json([
            self.mode,
            self.thread_id,
            self.commit_id,
            self.user_message_id,
            self.assistant_message_id,
            self.revision,
        ])


def get_log_path(dt: datetime | None = None) -> Path:
    """레거시 날짜 기준 로그 파일 경로. dt=None → 현재 시각."""
    LOGS_DIR.mkdir(exist_ok=True)
    return LOGS_DIR / f"{(dt or datetime.now()).strftime('%Y-%m-%d')}.md"


def append_turn(
    user_input: str,
    ai_response: str,
    timestamp: datetime | None = None,
) -> None:
    """레거시 Markdown 형식으로 1턴을 추가합니다. 임의 본문은 round-trip되지 않으므로
    확정 턴 기록에는 append_accepted_turn을 사용합니다. 쓰기 실패는 OSError로 전파됩니다."""
    path = get_log_path(timestamp)
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n---\n\n")
        f.write(f"[U]\n{user_input}\n\n")
        f.write(f"{ai_response}\n")


def _legacy_boundary(body: str) -> int | None:
    """레거시 레코드 본문에서 writer가 넣은 사용자/AI 경계 빈 줄 위치를 찾습니다.

    writer는 두 필드 사이에 빈 줄 하나만 넣고 필드 안의 빈 줄은 escape하지 않으므로,
    빈 줄이 정확히 하나일 때만 경계가 확정됩니다. 빈 줄이 둘 이상이면 어느 쪽이든 경계일 수
    있고, 뒤에 오는 날짜 헤더도 근거가 되지 않습니다(사용자 입력 속 헤더형 줄과 여러 문단
    응답은 바이트가 같다). 그런 경우와 빈 줄이 없는 경우는 None입니다.
    """
    first = body.find("\n\n")
    if first == -1 or body.find("\n\n", first + 1) != -1:
        return None
    return first


def _legacy_date_header_boundary(body: str) -> int | None:
    """역사적 관례로 경계를 고릅니다: AI 날짜 헤더가 바로 뒤따르는 첫 빈 줄.

    opt-in 호환 읽기 전용이며 정확성은 보장하지 않습니다. 사용자 입력이 빈 줄 뒤 헤더형 줄을
    담고 있으면 그 지점이 선택되고, 헤더 앞 엔진 블록 뒤에 빈 줄이 있으면 그 블록은 사용자
    쪽으로 갑니다. 조건을 만족하는 빈 줄이 없으면 None입니다.
    """
    position = body.find("\n\n")
    while position != -1:
        if _LEGACY_AI_HEADER_RE.match(body, position + 2):
            return position
        position = body.find("\n\n", position + 1)
    return None


def parse_log_file(path: Path, legacy_date_header: bool = False) -> list[dict]:
    """레거시 로그 파일을 파싱해 {user_input, ai_response} 딕셔너리 목록을 반환한다.

    writer 구분자로 레코드를 나누고 writer가 붙인 마지막 줄바꿈만 떼므로, 헤더 없는 응답·
    헤더 앞 엔진 블록·본문 속 ---도 잘리지 않는다. 경계를 정할 수 없는 레코드나 첫 레코드
    앞의 알 수 없는 내용은 ConversationLogError로 알린다. 파일은 읽기만 한다.

    legacy_date_header=True는 과거 기록을 읽기 위한 명시적 호환 옵션이다. 빈 줄이 하나뿐인
    레코드는 그대로 정확히 나누고, 그 밖의 레코드만 역사적 날짜 헤더 관례로 나눈다. 이때 각
    행에 "boundary"("unique_blank_line" 또는 "date_header_convention")를 붙이고, 관례로 나눈
    레코드가 있으면 UserWarning으로 알린다. 관례로도 나눌 수 없는 레코드와 알 수 없는 앞부분은
    기본 모드와 똑같이 예외다. 기본값(False)의 동작과 반환 형태는 바뀌지 않는다.
    """
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    leading, *records = text.split(_LEGACY_RECORD_SEPARATOR)
    if leading.strip():
        raise ConversationLogError(
            f"Unrecognized legacy log content before the first record in {path}"
        )
    turns = []
    assumed = 0
    for number, record in enumerate(records, start=1):
        body = record[:-1] if record.endswith("\n") else record
        boundary = _legacy_boundary(body)
        kind = "unique_blank_line"
        if boundary is None and legacy_date_header:
            boundary = _legacy_date_header_boundary(body)
            kind = "date_header_convention"
            if boundary is None:
                raise ConversationLogError(
                    f"Ambiguous legacy record {number} in {path}: it has none or several "
                    "blank lines and no blank line is followed by an AI date header "
                    "(**YYYY년), so even the historical date-header convention cannot "
                    "separate the fields"
                )
        if boundary is None:
            raise ConversationLogError(
                f"Ambiguous legacy record {number} in {path}: the old Markdown format "
                "separates user and AI text with one blank line, but this record has "
                "none or several, so the two fields cannot be recovered exactly"
            )
        row = {"user_input": body[:boundary], "ai_response": body[boundary + 2:]}
        if legacy_date_header:
            row["boundary"] = kind
            assumed += kind == "date_header_convention"
        turns.append(row)
    if assumed:
        warnings.warn(
            f"{path}: {assumed} of {len(turns)} legacy records were split by the historical "
            "date-header convention (AI text starts at the first **YYYY년 header after a "
            "blank line). User text containing such a header line would be misattributed, "
            "so field ownership for those records is assumed, not verified.",
            UserWarning,
            stacklevel=2,
        )
    return turns


def accepted_turns_path(thread_id: str) -> Path:
    """thread별 accepted-turn JSONL 경로를 반환합니다. 경로를 벗어나는 ID는 거부합니다."""
    name = str(thread_id or "").strip()
    if not name or name in {".", ".."} or ":" in name or Path(name).name != name:
        raise ConversationLogError(f"Invalid thread_id for accepted-turn log: {thread_id!r}")
    return LOGS_DIR / "accepted_turns" / f"{name}.jsonl"


def read_accepted_turns(path: Path) -> list[AcceptedTurn]:
    """accepted-turn JSONL을 읽어 검증합니다.

    파일이 없으면 아직 확정된 턴이 없는 thread이므로 빈 목록을 반환합니다. 읽기 실패,
    손상된 줄, 저장된 event_id 불일치는 빈 결과로 삼키지 않고 ConversationLogError로 알립니다.
    같은 프로세스의 추가가 끝나기 전 반쯤 쓰인 줄을 읽지 않도록 추가와 같은 잠금 안에서 읽습니다.
    """
    with _ACCEPTED_LOG_LOCK:
        return _read_accepted_turns_unlocked(path)


def _read_accepted_turns_unlocked(path: Path) -> list[AcceptedTurn]:
    """잠금을 이미 쥔 호출자를 위해 accepted-turn JSONL을 읽어 검증합니다."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ConversationLogError(f"Cannot read accepted-turn log {path}: {exc}") from exc

    # JSON이 본문의 \n·\r을 escape하므로 레코드 경계는 \n뿐이다. splitlines()는
    # U+2028 같은 본문 문자에서도 끊으므로 쓰지 않는다.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    turns: list[AcceptedTurn] = []
    for number, line in enumerate(lines, start=1):
        try:
            data = json.loads(line)
            turn = AcceptedTurn.model_validate(data)
        except (ValueError, ValidationError) as exc:
            raise ConversationLogError(
                f"Malformed accepted-turn record at {path}:{number}: {exc}"
            ) from exc
        if data.get("event_id") != turn.event_id:
            raise ConversationLogError(
                f"Accepted-turn record at {path}:{number} has a mismatched event_id"
            )
        turns.append(turn)
    return turns


def append_accepted_turn(turn: AcceptedTurn) -> bool:
    """같은 event_id가 thread 로그에 없을 때만 한 줄을 추가합니다.

    추가하면 True, 이미 기록된 재시도라면 False를 반환합니다. 읽기·쓰기 실패는
    ConversationLogError로 전파되어 호출자가 확정 단계를 완료로 표시하지 않게 합니다.
    중복 검사부터 fsync까지 한 잠금 안에서 수행하므로 같은 프로세스의 두 worker가 같은
    이벤트를 동시에 확정해도 한 줄만 남습니다. 호출자는 이 함수를 이벤트 루프 밖
    (asyncio.to_thread 또는 동기 route)에서 부릅니다.
    """
    path = accepted_turns_path(turn.thread_id)
    with _ACCEPTED_LOG_LOCK:
        existing_turns = _read_accepted_turns_unlocked(path)
        if any(existing.event_id == turn.event_id for existing in existing_turns):
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(turn.model_dump_json() + "\n")
                handle.flush()
                # 호출자는 이 반환 직후 확정 단계를 완료로 기록하므로 디스크 반영을 먼저 보장한다.
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ConversationLogError(f"Cannot write accepted-turn log {path}: {exc}") from exc
    return True
