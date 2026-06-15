# ================================
# src/apps/graph_viewer/__init__.py
#
# 그래프 뷰어 백엔드 패키지의 공개 인터페이스를 제공합니다.
#
# Functions
#   - ensure_graph_server() -> str : 그래프 뷰어 서버를 시작하고 URL을 반환합니다.
#   - run() -> None : 그래프 뷰어 서버를 실행합니다.
#   - update_graph_snapshot(graph: GraphSnapshot | dict[str, Any]) -> None : 최신 그래프 스냅샷을 캐시에 반영합니다.
# ================================

from __future__ import annotations

from src.apps.graph_viewer.models import GraphSnapshot
from src.apps.graph_viewer.server import ensure_graph_server, run, update_graph_snapshot

__all__ = ["GraphSnapshot", "ensure_graph_server", "run", "update_graph_snapshot"]


def __dir__() -> list[str]:
    """공개 심볼 목록을 반환합니다."""
    return sorted(__all__)
