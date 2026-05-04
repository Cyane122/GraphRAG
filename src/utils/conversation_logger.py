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
_AI_HEADER_RE = re.compile(r"\*\*(?:제국력\s+)?\d{4}년")


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
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n---\n", text)
    turns = []

    for block in blocks:
        block = block.strip()
        if not block or "[U]" not in block:
            continue

        # [U]\n 이후 ~ 다음 빈 줄 + **(제국력) YYYY년 이전까지를 유저 입력으로 매칭
        # (?=\n\n\*\*(?:제국력\s+)?\d{4}년) 부분을 수정함
        u_match = re.search(r"\[U\]\n(.*?)(?=\n\n\*\*(?:제국력\s+)?\d{4}년)", block, re.DOTALL)
        if not u_match:
            # 헤더 형식이 안 맞으면 블록 전체를 뒤져서라도 ai_match 위치를 찾아야 함
            ai_match = _AI_HEADER_RE.search(block)
            if ai_match:
                # [U] 이후부터 ai_match 시작 전까지를 user_input으로 간주
                user_input = block[block.find("[U]")+3 : ai_match.start()].strip()
            else:
                continue
        else:
            user_input = u_match.group(1).strip()

        # AI 응답 파싱
        ai_match = _AI_HEADER_RE.search(block)
        if not ai_match:
            continue

        ai_response = block[ai_match.start():].strip()

        if user_input and ai_response:
            turns.append({"user_input": user_input, "ai_response": ai_response})

    return turns