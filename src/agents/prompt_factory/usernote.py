# ================================
# src/agents/prompt_factory/usernote.py
#
# 스레드별 유저노트 로드/저장 및 최우선 프롬프트 블록 생성.
# data/threads/{thread_id}/usernote.md에 저장되며,
# 세계관·캐릭터 설정을 포함한 모든 프롬프트보다 우선 적용됩니다.
#
# Functions
#   - get_usernote_path(thread_id: str) -> Path : 유저노트 파일 경로 반환
#   - load_usernote(thread_id: str) -> str : 유저노트 로드 (없으면 빈 문자열)
#   - save_usernote(thread_id: str, content: str) -> None : 유저노트 저장
#   - build_usernote_block(content: str) -> str : 최우선 프롬프트 블록 생성
# ================================

from pathlib import Path

_THREADS_DIR = Path("data/threads")


def get_usernote_path(thread_id: str) -> Path:
    """유저노트 파일 경로를 반환합니다."""
    return _THREADS_DIR / thread_id / "usernote.md"


def load_usernote(thread_id: str) -> str:
    """유저노트 파일을 읽어 반환합니다. 파일이 없으면 빈 문자열을 반환합니다."""
    path = get_usernote_path(thread_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_usernote(thread_id: str, content: str) -> None:
    """유저노트를 파일에 즉시 저장합니다."""
    path = get_usernote_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_usernote_block(content: str) -> str:
    """유저노트를 최우선 지시사항 블록으로 감싸 반환합니다.

    내용이 비어 있으면 빈 문자열을 반환합니다.
    반환된 블록은 dynamic_prompt 맨 앞에 삽입되어 모든 프롬프트보다 우선합니다.
    """
    if not content:
        return ""
    return (
        "<user_directives>\n"
        "※ 아래 지시사항은 세계관·캐릭터 설정·씬 분류를 포함한 모든 프롬프트보다 우선합니다.\n\n"
        f"{content}\n"
        "</user_directives>\n\n"
    )
