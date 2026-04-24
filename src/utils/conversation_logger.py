# src/utils/conversation_logger.py
"""
대화 내용을 날짜별 Markdown 파일로 저장하고 불러오는 유틸리티.

저장 형식 (logs/YYYY-MM-DD.md):

    ---

    [U]
    유저 입력 (멀티라인 허용)

    **YYYY년 M월 D일 요일 HH시 MM분, 장소**
    AI 응답 본문...

파서는 [U] 마커 + AI 헤더 패턴 (**YYYY년) 으로 경계를 감지.
"""

import re
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path("logs")
_AI_HEADER_RE = re.compile(r"\*\*\d{4}년")


def get_log_path(dt: datetime | None = None) -> Path:
    """날짜 기준 로그 파일 경로. dt=None → 현재 시각."""
    LOGS_DIR.mkdir(exist_ok=True)
    return LOGS_DIR / f"{(dt or datetime.now()).strftime('%Y-%m-%d')}.md"


def append_turn(
    user_input: str,
    ai_response: str,
    timestamp: datetime | None = None,
) -> None:
    """대화 1턴을 로그 파일에 추가."""
    path = get_log_path(timestamp)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n---\n\n")
            f.write(f"[U]\n{user_input}\n\n")
            f.write(f"{ai_response}\n")
    except OSError as e:
        print(f"[ConversationLogger] 저장 실패: {e}")


def parse_log_file(path: Path) -> list[dict]:
    """
    로그 파일을 파싱해 턴 목록 반환.

    반환: [{"user_input": str, "ai_response": str}, ...]
    """
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n---\n", text)
    turns = []

    for block in blocks:
        block = block.strip()
        if not block or "[U]" not in block:
            continue

        # [U]\n 이후 ~ 다음 빈 줄 + **YYYY년 이전 = 유저 입력
        u_match = re.search(r"\[U\]\n(.*?)(?=\n\n\*\*\d{4}년)", block, re.DOTALL)
        if not u_match:
            continue

        user_input = u_match.group(1).strip()

        # **YYYY년 이후 전부 = AI 응답
        ai_match = _AI_HEADER_RE.search(block)
        if not ai_match:
            continue

        ai_response = block[ai_match.start():].strip()

        if user_input and ai_response:
            turns.append({"user_input": user_input, "ai_response": ai_response})

    return turns