# ================================
# src/apps/app/live_console.py
#
# 웹 콘솔에 표시할 프로세스 로그를 메모리에 보관합니다.
#
# Classes
#   - ConsoleEntry : 한 줄의 실시간 콘솔 로그
#   - LiveConsoleBuffer : 최근 로그를 순서대로 보관하는 스레드 안전 버퍼
#   - ConsoleTeeStream : stdout/stderr를 원래 출력과 로그 버퍼로 동시에 전달
#
# Functions
#   - configure_live_console() -> None : stdout/stderr 실시간 캡처를 한 번 설치합니다.
#   - get_live_console() -> LiveConsoleBuffer : 프로세스 전역 콘솔 버퍼를 반환합니다.
# ================================

from __future__ import annotations

import re
import sys
from collections import deque
from datetime import datetime
from threading import Lock
from typing import TextIO
from uuid import uuid4

from pydantic import BaseModel

_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_RE = re.compile(
    r"(?i)((?:[\"']?)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|private[_-]?key)(?:[\"']?)\s*[:=]\s*[\"']?)([^\s,;\"']+)"
)
_CREDENTIAL_RE = re.compile(r"\b(?:sk[-_]|hf_)[A-Za-z0-9_-]{10,}\b")
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~-]{10,}")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:key|token|access_token|api_key)=)[^&\s]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_OPAQUE_SECRET_RE = re.compile(
    r"\b(?:AKIA|AIza)[A-Za-z0-9_/-]{12,}\b|^[A-Za-z0-9+/]{80,}={0,2}$|^-+.*PRIVATE KEY-+$"
)
_MAX_LINE_CHARS = 16_000


class ConsoleEntry(BaseModel):
    """한 줄의 순서, 시각, 출력 스트림, 레벨, 메시지를 표현합니다."""

    seq: int
    timestamp: str
    stream: str
    level: str
    message: str


class LiveConsoleBuffer:
    """최근 프로세스 로그를 제한된 크기의 메모리 버퍼에 보관합니다."""

    def __init__(self, max_entries: int = 1200) -> None:
        """최대 보관 개수를 지정해 빈 로그 버퍼를 생성합니다."""
        self._entries: deque[ConsoleEntry] = deque(maxlen=max_entries)
        self._lock = Lock()
        self._next_seq = 1
        self.instance_id = uuid4().hex

    def append(self, stream: str, message: str) -> None:
        """비어 있지 않은 로그 한 줄을 마스킹한 뒤 버퍼에 추가합니다."""
        if not message.strip():
            return
        safe_message = _redact_secrets(message.rstrip())
        if len(safe_message) > _MAX_LINE_CHARS:
            safe_message = f"{safe_message[:_MAX_LINE_CHARS]} … [truncated]"
        level_match = _LEVEL_RE.search(safe_message)
        level = level_match.group(1).lower() if level_match else ("error" if stream == "stderr" else "info")
        with self._lock:
            entry = ConsoleEntry(
                seq=self._next_seq,
                timestamp=datetime.now().isoformat(timespec="milliseconds"),
                stream=stream,
                level=level,
                message=safe_message,
            )
            self._next_seq += 1
            self._entries.append(entry)

    def entries_after(self, after: int, limit: int = 250) -> list[ConsoleEntry]:
        """지정한 순번 이후의 로그를 반환하며 최초 요청은 최근 항목만 반환합니다."""
        with self._lock:
            entries = list(self._entries)
        if after <= 0:
            return entries[-limit:]
        return [entry for entry in entries if entry.seq > after][:limit]

    def latest_seq(self) -> int:
        """가장 최근 로그 순번을 반환하며 아직 로그가 없으면 0을 반환합니다."""
        with self._lock:
            return self._next_seq - 1


class ConsoleTeeStream:
    """텍스트 스트림 출력을 보존하면서 완성된 줄을 콘솔 버퍼에 복제합니다."""

    def __init__(self, wrapped: TextIO, stream_name: str, buffer: LiveConsoleBuffer) -> None:
        """원본 스트림과 복제 대상 버퍼를 연결합니다."""
        self._wrapped = wrapped
        self._stream_name = stream_name
        self._buffer = buffer
        self._pending = ""
        self._lock = Lock()
        self._graphrag_live_console = True

    @property
    def encoding(self) -> str | None:
        """원본 스트림 인코딩을 반환합니다."""
        return getattr(self._wrapped, "encoding", None)

    @property
    def errors(self) -> str | None:
        """원본 스트림 오류 처리 모드를 반환합니다."""
        return getattr(self._wrapped, "errors", None)

    def write(self, text: str) -> int:
        """원본에 텍스트를 쓰고 줄바꿈까지 완성된 로그를 버퍼에 추가합니다."""
        written = self._wrapped.write(text)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        with self._lock:
            chunks = (self._pending + normalized).split("\n")
            self._pending = chunks.pop()
        for line in chunks:
            self._buffer.append(self._stream_name, line)
        return written

    def flush(self) -> None:
        """원본 스트림의 대기 출력을 비웁니다."""
        self._wrapped.flush()

    def isatty(self) -> bool:
        """원본 스트림의 터미널 연결 여부를 반환합니다."""
        return self._wrapped.isatty()

    def fileno(self) -> int:
        """원본 스트림의 파일 디스크립터를 반환합니다."""
        return self._wrapped.fileno()

    def __getattr__(self, name: str) -> object:
        """직접 구현하지 않은 스트림 속성을 원본 스트림에 위임합니다."""
        return getattr(self._wrapped, name)


_LIVE_CONSOLE = LiveConsoleBuffer()


def _redact_secrets(message: str) -> str:
    """터미널 제어문자를 제거하고 일반적인 키·토큰 형태를 마스킹합니다."""
    redacted = _ANSI_RE.sub("", message)
    redacted = _SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = _CREDENTIAL_RE.sub("[redacted]", redacted)
    redacted = _BEARER_RE.sub(r"\1[redacted]", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = _JWT_RE.sub("[redacted]", redacted)
    return _OPAQUE_SECRET_RE.sub("[redacted]", redacted)


def configure_live_console() -> None:
    """stdout과 stderr에 로그 복제 스트림을 중복 없이 설치합니다."""
    if not getattr(sys.stdout, "_graphrag_live_console", False):
        sys.stdout = ConsoleTeeStream(sys.stdout, "stdout", _LIVE_CONSOLE)
    if not getattr(sys.stderr, "_graphrag_live_console", False):
        sys.stderr = ConsoleTeeStream(sys.stderr, "stderr", _LIVE_CONSOLE)


def get_live_console() -> LiveConsoleBuffer:
    """프로세스 전역 실시간 콘솔 버퍼를 반환합니다."""
    return _LIVE_CONSOLE
