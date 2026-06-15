# ================================
# src/agents/prompt_factory/usernote.py
#
# 스레드별 유저노트 로드/저장 (레거시 단일 파일 지원 + 신규 다중 노트 빌더).
# 신규: ConversationState.usernotes 리스트에서 <usernote> 블록 생성.
# 레거시: data/threads/{thread_id}/usernote.md (deprecated, 신규 전환 후 미사용).
#
# Functions
#   - get_usernote_path(thread_id: str) -> Path : 레거시 유저노트 파일 경로 반환
#   - load_usernote(thread_id: str) -> str : 레거시 유저노트 로드 (없으면 빈 문자열)
#   - save_usernote(thread_id: str, content: str) -> None : 레거시 유저노트 저장
#   - build_usernote_block(content: str) -> str : 레거시 단일 블록 생성 (deprecated)
#   - build_usernotes_block(notes: list[dict]) -> str : 활성화된 노트들을 <usernote> 태그로 조합
# ================================

from pathlib import Path

_THREADS_DIR = Path("data/threads")


def get_usernote_path(thread_id: str) -> Path:
    """레거시 유저노트 파일 경로를 반환합니다."""
    return _THREADS_DIR / thread_id / "usernote.md"


def load_usernote(thread_id: str) -> str:
    """레거시 유저노트 파일을 읽어 반환합니다. 파일이 없으면 빈 문자열을 반환합니다."""
    path = get_usernote_path(thread_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_usernote(thread_id: str, content: str) -> None:
    """레거시 유저노트를 파일에 즉시 저장합니다."""
    path = get_usernote_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_usernote_block(content: str) -> str:
    """[deprecated] 레거시 단일 유저노트를 <user_directives> 블록으로 감싸 반환합니다.

    신규 다중 노트 시스템(build_usernotes_block)으로 마이그레이션되었습니다.
    이 함수는 하위 호환성을 위해 잔존합니다.
    """
    if not content:
        return ""
    return (
        "<user_directives>\n"
        "※ 아래 지시사항은 세계관·캐릭터 설정·씬 분류를 포함한 모든 프롬프트보다 우선합니다.\n\n"
        f"{content}\n"
        "</user_directives>\n\n"
    )


def build_usernotes_block(notes: list[dict]) -> str:
    """활성화된 유저노트 목록을 <usernote> 태그 블록으로 조합해 반환합니다.

    활성화된 노트(enabled=True)만 포함하며, 노트가 없으면 빈 문자열을 반환합니다.
    반환된 블록은 effective_input 맨 앞에 prepend되어 Player Input 바로 위에 삽입됩니다.
    """
    active = [n for n in (notes or []) if n.get("enabled")]
    if not active:
        return ""
    parts = [
        f'<usernote name="{n["name"]}">\n{n["content"]}\n</usernote>'
        for n in active
    ]
    return "\n".join(parts)
