# ================================
# src/simulation/state/models.py
#
# Graph와 Wiki가 공유하는 accepted-turn Updater 요청과 결과 모델을 정의합니다.
#
# Classes
#   - GraphTurnUpdateRequest : Graph 상태 반영에 필요한 accepted turn 입력
#   - WikiTurnUpdateRequest : Wiki commit 계획·보류에 필요한 accepted turn 입력
#   - TurnUpdateResult : mode별 상태 반영 결과
# ================================

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from src.wiki.models import PendingWikiCommit


UpdaterMode = Literal["graph", "wiki"]


class GraphTurnUpdateRequest(BaseModel):
    """Graph accepted turn을 영속 상태에 반영하기 위한 입력입니다."""

    mode: Literal["graph"] = "graph"
    actor_response: str
    npc_id: str
    pc_id: str
    scene_types: list[str] | None = None
    scene_chars: list[str] | None = None
    world_config: dict | None = None
    manager_effects: dict | None = None
    history_snapshot: list[dict] | None = None
    recent_snapshot: list[str] | None = None
    thread_id: str | None = None
    commit_id: str | None = None
    user_input: str = ""


class WikiTurnUpdateRequest(BaseModel):
    """Wiki accepted turn에서 검증된 pending commit을 만들기 위한 입력입니다."""

    mode: Literal["wiki"] = "wiki"
    vault_root: Path
    thread_id: str
    user_input: str
    actor_response: str
    model_name: str
    max_attempts: int = 3
    player_profile_id: str = ""
    actor_profile_id: str = ""
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    wiki_systems: dict[str, bool] | None = None


class TurnUpdateResult(BaseModel):
    """Graph OOC 또는 Wiki pending commit을 담는 mode-aware 결과입니다."""

    mode: UpdaterMode
    ooc_message: str | None = None
    pending_wiki_commit: PendingWikiCommit | None = None
