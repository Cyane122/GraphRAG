# ================================
# src/ui/graph_server.py
#
# Chainlit 채팅 UI와 분리된 로컬 그래프 관찰 서버를 제공합니다.
#
# Functions
#   - ensure_graph_server() -> str : 그래프 서버를 시작하고 URL을 반환합니다.
#   - update_graph_snapshot(graph) -> None : 최신 그래프 스냅샷을 캐시에 반영합니다.
#
# API Endpoints
#   GET  /graph.json       : 현재 그래프 스냅샷 JSON
#   GET  /api/threads      : DB가 있는 스레드 목록
#   POST /api/load         : {"threadId": "..."} → 해당 스레드 로드 후 스냅샷 반환
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
    """현재 캐시된 그래프 스냅샷을 JSON 응답 바이트로 직렬화합니다."""
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
        path = urlparse(self.path).path

        if path == "/graph.json":
            self._send(200, "application/json; charset=utf-8", _snapshot_payload())
            return

        if path == "/api/threads":
            self._send_threads()
            return

        static_path = _safe_static_path(self.path)
        if static_path is None:
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        self._send(200, content_type, static_path.read_bytes())

    def do_POST(self) -> None:
        """POST 요청을 처리합니다."""
        path = urlparse(self.path).path
        if path == "/api/load":
            self._handle_load()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send_threads(self) -> None:
        """스레드 목록 API 응답을 전송합니다."""
        try:
            from src.ui.graph_loader import list_threads
            threads = list_threads()
            payload = json.dumps(threads, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def _handle_load(self) -> None:
        """요청된 스레드 DB를 그래프 스냅샷으로 로드합니다."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            thread_id = body.get("threadId", "")
            if not thread_id:
                self._send(400, "text/plain; charset=utf-8", b"missing threadId")
                return

            from src.ui.graph_loader import build_graph_from_thread
            graph = build_graph_from_thread(thread_id)
            update_graph_snapshot(graph)
            payload = json.dumps(graph.model_dump(by_alias=True), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        """개발용 서버의 기본 요청 로그 출력을 끕니다."""
        return

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        """HTTP 응답을 공통 헤더와 함께 전송합니다."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)


def ensure_graph_server() -> str:
    """그래프 서버를 시작하고 접속 URL을 반환합니다."""
    global _SERVER
    url = f"http://{_HOST}:{_PORT}"
    if _SERVER is not None:
        return url
    try:
        _SERVER = ThreadingHTTPServer((_HOST, _PORT), _GraphHandler)
    except OSError:
        return url
    thread = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    thread.start()
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
