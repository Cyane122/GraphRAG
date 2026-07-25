# ================================
# tests/smoke_mode_aware_updater.py
#
# 단일 accepted-turn Updater가 Graph와 Wiki mode를 모두 분기하는지 검증합니다.
#
# Functions
#   - _run() -> None : 두 mode의 호출·결과·Wiki commit 보류를 검증합니다.
# ================================

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.simulation.state.models import (
    GraphTurnUpdateRequest,
    WikiTurnUpdateRequest,
)
from src.simulation.state.updater import update_accepted_turn
from src.wiki.commit import WikiCommitQueue
from src.wiki.models import PendingWikiCommit
from src.wiki.store import WikiStore


async def _run() -> None:
    """공개 Updater 하나가 Graph 반영기와 Wiki commit planner를 선택하는지 검증합니다."""
    assert "src.simulation.state.graph_apply" not in sys.modules
    with TemporaryDirectory(prefix="mode_aware_updater_") as temporary_directory:
        vault_root = Path(temporary_directory)
        thread_root = vault_root / "threads" / "wiki-thread"
        thread_root.mkdir(parents=True)
        planned = PendingWikiCommit(
            user_input_hash="user",
            actor_response_hash="actor",
            updater_model="mock",
        )
        planner = AsyncMock(return_value=planned)
        postprocessors = AsyncMock(return_value="wiki-ooc")
        with (
            patch(
                "src.wiki.context.read_wiki_thread_documents",
                return_value=[],
            ),
            patch(
                "src.wiki.plan_pending_commit",
                new=planner,
            ),
            patch(
                "src.wiki.postprocess.apply_wiki_postprocessors",
                new=postprocessors,
            ),
        ):
            wiki_result = await update_accepted_turn(
                WikiTurnUpdateRequest(
                    vault_root=vault_root,
                    thread_id="wiki-thread",
                    user_input="wiki input",
                    actor_response="accepted wiki prose",
                    model_name="mock",
                )
            )

        assert wiki_result.mode == "wiki"
        assert wiki_result.ooc_message == "wiki-ooc"
        assert wiki_result.pending_wiki_commit == planned
        planner.assert_awaited_once()
        postprocessors.assert_awaited_once()
        queued = WikiCommitQueue(WikiStore(thread_root)).load()
        assert queued is not None and queued.commit_id == planned.commit_id

    assert "src.simulation.state.graph_apply" not in sys.modules
    graph_apply = AsyncMock(return_value="graph-ooc")
    with patch(
        "src.simulation.state.graph_apply.apply_graph_actor_response",
        new=graph_apply,
    ):
        graph_result = await update_accepted_turn(
            GraphTurnUpdateRequest(
                actor_response="accepted graph prose",
                npc_id="npc",
                pc_id="pc",
                user_input="graph input",
            )
        )
    assert graph_result.mode == "graph"
    assert graph_result.ooc_message == "graph-ooc"
    assert graph_result.pending_wiki_commit is None
    graph_apply.assert_awaited_once()

    assert not (ROOT / "src" / "wiki" / "updater.py").exists()


if __name__ == "__main__":
    asyncio.run(_run())
    print("smoke_mode_aware_updater: ok")
