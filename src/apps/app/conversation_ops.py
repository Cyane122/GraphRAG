# ================================
# src/apps/app/conversation_ops.py
#
# ConversationOps 프로토콜과 world_mode(Graph/Wiki)별 reroll/edit/activate/delete
# 구현의 두 항목짜리 레지스트리. routers/messages.py는 이 모듈의
# get_conversation_ops() 하나로 world_mode 분기를 대체한다 — 전송 계층에는
# 도메인 배선을 두지 않는다(AGENTS.md "app 진입 모듈은 얇게").
#
# Classes
#   - ConversationOps : 한 world_mode의 reroll/edit/activate/delete 구현이 만족해야 하는 구조적 프로토콜.
#
# Functions
#   - get_conversation_ops(world_mode: WorldMode) -> ConversationOps : state.world_mode에서 구현체를 한 번 해석합니다.
# ================================

from __future__ import annotations

from typing import Protocol

from src.apps.app.message_ops import (
    activate_variant,
    delete_message,
    edit_message,
    reroll_assistant,
)
from src.apps.app.models import ConversationState, WorldMode
from src.apps.app.storage import ConversationStore
from src.apps.app.wiki_message_ops import (
    activate_wiki_variant,
    delete_wiki_message,
    edit_wiki_message,
    reroll_wiki_assistant,
)


class ConversationOps(Protocol):
    """한 world_mode의 메시지 리롤/수정/버전 활성화/삭제 구현이 만족해야 하는 프로토콜.

    ``activate``는 Graph(``activate_variant``)가 원래 sync, Wiki
    (``activate_wiki_variant``)가 원래 async였다. Wiki 쪽은 Wiki Markdown
    변경안 재생성이라는 실제 I/O를 하므로 async가 필수고, Graph 쪽은 순수
    인메모리 데이터 조작이라 async로 감싸도 실행 위치가 바뀌지 않는다(동작
    중립). 그래서 async로 통일했다.

    ``delete``는 두 구현 모두 원래 sync다. FastAPI는 sync `def` 엔드포인트를
    스레드풀에서 실행하므로, delete를 async로 올리고 엔드포인트도 async로
    바꾸면 그 블로킹 작업이 스레드풀 대신 이벤트 루프 스레드에서 직접 실행되는
    실질적인 실행 위치 변화가 생긴다. API 계약(요청/응답)은 같지만 이건
    "동작을 바꾸지 않는다"는 이 단계의 전제를 넘어서므로, delete는 sync로
    남기고 엔드포인트도 sync `def`로 유지한다.
    """

    async def reroll(
        self,
        state: ConversationState,
        assistant_id: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """assistant_id가 가리키는 응답을 재생성합니다."""
        ...

    async def edit(
        self,
        state: ConversationState,
        message_id: str,
        content: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """message_id가 가리키는 메시지를 새 content로 수정합니다."""
        ...

    async def activate(
        self,
        state: ConversationState,
        message_id: str,
        version_index: int,
        store: ConversationStore,
    ) -> dict:
        """message_id 응답의 저장된 버전 하나를 활성화합니다."""
        ...

    def delete(
        self,
        state: ConversationState,
        message_id: str,
        store: ConversationStore,
    ) -> dict:
        """message_id가 가리키는 메시지를 삭제합니다."""
        ...


class _GraphConversationOps:
    """Graph 엔진의 ConversationOps 구현. message_ops.py에 그대로 위임한다."""

    async def reroll(
        self,
        state: ConversationState,
        assistant_id: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """Graph 대화에서 assistant 응답을 재생성합니다."""
        return await reroll_assistant(state, assistant_id, store, actor_model=actor_model)

    async def edit(
        self,
        state: ConversationState,
        message_id: str,
        content: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """Graph 대화의 메시지를 수정합니다."""
        return await edit_message(state, message_id, content, store, actor_model=actor_model)

    async def activate(
        self,
        state: ConversationState,
        message_id: str,
        version_index: int,
        store: ConversationStore,
    ) -> dict:
        """Graph 대화의 저장된 버전을 활성화합니다(순수 데이터 조작 — async 래핑은 동작 중립)."""
        return activate_variant(state, message_id, version_index, store)

    def delete(
        self,
        state: ConversationState,
        message_id: str,
        store: ConversationStore,
    ) -> dict:
        """Graph 대화의 메시지를 삭제합니다."""
        return delete_message(state, message_id, store)


class _WikiConversationOps:
    """Wiki 엔진의 ConversationOps 구현. wiki_message_ops.py에 그대로 위임한다."""

    async def reroll(
        self,
        state: ConversationState,
        assistant_id: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """Wiki 대화에서 최신 미반영 응답을 재생성합니다."""
        return await reroll_wiki_assistant(state, assistant_id, store, actor_model=actor_model)

    async def edit(
        self,
        state: ConversationState,
        message_id: str,
        content: str,
        store: ConversationStore,
        *,
        actor_model: str | None = None,
    ) -> dict:
        """Wiki 대화의 최신 메시지를 수정합니다."""
        return await edit_wiki_message(state, message_id, content, store, actor_model=actor_model)

    async def activate(
        self,
        state: ConversationState,
        message_id: str,
        version_index: int,
        store: ConversationStore,
    ) -> dict:
        """Wiki 대화의 최신 응답 버전을 활성화합니다."""
        return await activate_wiki_variant(state, message_id, version_index, store)

    def delete(
        self,
        state: ConversationState,
        message_id: str,
        store: ConversationStore,
    ) -> dict:
        """Wiki 대화의 최신 메시지를 삭제합니다."""
        return delete_wiki_message(state, message_id, store)


_REGISTRY: dict[WorldMode, ConversationOps] = {
    "graph": _GraphConversationOps(),
    "wiki": _WikiConversationOps(),
}


def get_conversation_ops(world_mode: WorldMode) -> ConversationOps:
    """state.world_mode에서 ConversationOps 구현체를 한 번 해석합니다."""
    return _REGISTRY[world_mode]
