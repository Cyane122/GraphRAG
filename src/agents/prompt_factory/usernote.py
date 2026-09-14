# ================================
# src/agents/prompt_factory/usernote.py
#
# 스레드별 유저노트 블록 빌더.
# ConversationState.usernotes 리스트에서 <usernote> 블록을 생성합니다.
#
# Functions
#   - build_usernotes_block(notes: list[dict]) -> str : 활성화된 노트들을 <usernote> 태그로 조합
# ================================


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
