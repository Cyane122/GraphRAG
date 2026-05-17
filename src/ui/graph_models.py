# ================================
# src/ui/graph_models.py
#
# Graph debug viewer와 HTTP server 사이에서 주고받는 그래프 스냅샷 모델을 정의합니다.
#
# Classes
#   - GraphNode : 그래프 노드 표시 데이터
#   - GraphEdge : 그래프 엣지 표시 데이터
#   - GraphSnapshot : 그래프 관찰 창에 전달되는 전체 스냅샷
# ================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphNode(BaseModel):
    """그래프 뷰어에서 렌더링할 단일 노드입니다."""

    id: str
    label: str
    type: str
    subtitle: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """그래프 뷰어에서 렌더링할 단일 엣지입니다."""

    source: str
    target: str
    label: str
    details: dict[str, Any] = Field(default_factory=dict)


class GraphSnapshot(BaseModel):
    """별도 그래프 관찰 창에 제공되는 현재 그래프 상태입니다."""

    model_config = ConfigDict(populate_by_name=True)

    world_id: str = Field(default="", alias="worldId")
    generated_at: str = Field(default="", alias="generatedAt")
    visible_time: str | None = Field(default=None, alias="visibleTime")
    committed_time: str | None = Field(default=None, alias="committedTime")
    pending_time: str | None = Field(default=None, alias="pendingTime")
    time_source: str = Field(default="none", alias="timeSource")
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
