# ================================
# src/ui/graph_server.py
#
# Chainlit 채팅 UI와 분리된 로컬 그래프 관찰 서버를 제공합니다.
#
# Functions
#   - ensure_graph_server() -> str : 그래프 서버를 시작하고 URL을 반환합니다.
#   - update_graph_snapshot(graph: GraphSnapshot | dict[str, Any]) -> None : 최신 그래프 스냅샷을 서버 캐시에 반영합니다.
# ================================

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from src.ui.graph_models import GraphSnapshot

_HOST = "127.0.0.1"
_PORT = 8765
_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public" / "graph"
_SERVER: ThreadingHTTPServer | None = None
_LOCK = threading.Lock()
_LATEST_GRAPH: dict[str, Any] = GraphSnapshot().model_dump(by_alias=True)


def _snapshot_payload() -> bytes:
    """최신 그래프 스냅샷을 JSON 응답 바이트로 직렬화합니다."""
    with _LOCK:
        return json.dumps(_LATEST_GRAPH, ensure_ascii=False).encode("utf-8")


def _safe_static_path(request_path: str) -> Path | None:
    """요청 경로를 public/graph 하위 정적 파일 경로로 제한합니다."""
    parsed_path = unquote(urlparse(request_path).path)
    relative = "index.html" if parsed_path in {"/", "/index.html"} else parsed_path.lstrip("/")
    candidate = (_PUBLIC_DIR / relative).resolve()
    try:
        candidate.relative_to(_PUBLIC_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


class _GraphHandler(BaseHTTPRequestHandler):
    """그래프 관찰 창의 정적 파일과 JSON 스냅샷을 제공하는 HTTP 핸들러입니다."""

    def do_GET(self) -> None:
        """GET 요청을 처리합니다."""
        if urlparse(self.path).path == "/graph.json":
            self._send(200, "application/json; charset=utf-8", _snapshot_payload())
            return

        static_path = _safe_static_path(self.path)
        if static_path is None:
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        self._send(200, content_type, static_path.read_bytes())

    def log_message(self, format: str, *args: Any) -> None:
        """개발용 서버의 기본 요청 로그 출력을 끕니다."""
        return

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        """HTTP 응답을 전송합니다."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def ensure_graph_server() -> str:
    """그래프 서버를 시작하고 접속 URL을 반환합니다."""
    global _SERVER
    if _SERVER is not None:
        return f"http://{_HOST}:{_PORT}"
    try:
        _SERVER = ThreadingHTTPServer((_HOST, _PORT), _GraphHandler)
    except OSError:
        return f"http://{_HOST}:{_PORT}"
    thread = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    thread.start()
    url = f"http://{_HOST}:{_PORT}"
    print(f"[graph] 그래프 관찰 창: {url}")
    return url


def update_graph_snapshot(graph: GraphSnapshot | dict[str, Any]) -> None:
    """최신 그래프 스냅샷을 서버 캐시에 반영합니다."""
    if isinstance(graph, GraphSnapshot):
        payload = graph.model_dump(by_alias=True)
    else:
        payload = GraphSnapshot.model_validate(graph).model_dump(by_alias=True)

    with _LOCK:
        _LATEST_GRAPH.clear()
        _LATEST_GRAPH.update(payload)
