# ================================
# src/ui/graph_server.py
#
# Chainlit 채팅 UI와 분리된 로컬 그래프 관찰 서버를 제공합니다.
#
# Functions
#   - ensure_graph_server() -> str : 그래프 서버를 시작하고 URL을 반환합니다.
#   - get_cached_graph_snapshot() -> dict[str, Any] : 캐시된 그래프 스냅샷 복사본을 반환합니다.
#   - update_graph_snapshot(graph) -> None : 최신 그래프 스냅샷을 캐시에 반영합니다.
#
# API Endpoints
#   GET   /graph.json         : 현재 그래프 스냅샷 JSON
#   GET   /api/threads        : DB가 있는 스레드 목록
#   GET   /api/schema         : ?threadId=... → Kuzu 테이블 스키마 목록
#   POST  /api/load           : {"threadId": "..."} → DB 직접 로드 (lock 시 캐시 폴백)
#   PATCH /api/node           : {"threadId","nodeId","updates"} → 노드 속성 수정 후 갱신 스냅샷 반환
#   PATCH /api/edge           : {"threadId","source","target","updates"} → 엣지 속성 수정
#
# Static files
#   /  /index.html  /ppt_viewer.html : frontend/ppt_viewer.html
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
_PORT = 8766
_DEFAULT_VIEWER = "ppt_viewer.html"
_ROOT_VIEWER = Path(__file__).resolve().parents[2] / "frontend" / "ppt_viewer.html"
_SERVER: ThreadingHTTPServer | None = None
_LOCK = threading.Lock()
_LATEST_GRAPH: dict[str, Any] = GraphSnapshot().model_dump(by_alias=True)


def _snapshot_payload() -> bytes:
    """현재 캐시된 그래프 스냅샷을 JSON 응답 바이트로 직렬화합니다."""
    with _LOCK:
        return json.dumps(_LATEST_GRAPH, ensure_ascii=False).encode("utf-8")


def get_cached_graph_snapshot() -> dict[str, Any]:
    """현재 캐시된 그래프 스냅샷의 복사본을 반환합니다."""
    with _LOCK:
        return dict(_LATEST_GRAPH)


def _safe_static_path(request_path: str) -> Path | None:
    """요청 경로를 정적 파일 경로로 변환합니다."""
    parsed_path = unquote(urlparse(request_path).path)
    if parsed_path in {"/", "/index.html", f"/{_DEFAULT_VIEWER}"}:
        return _ROOT_VIEWER if _ROOT_VIEWER.is_file() else None
    return None


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

        if path == "/api/schema":
            self._send_schema()
            return

        static_path = _safe_static_path(self.path)
        if static_path is None:
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        self._send(200, content_type, static_path.read_bytes())

    def do_OPTIONS(self) -> None:
        """CORS preflight 요청에 응답합니다."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        """POST 요청을 처리합니다."""
        path = urlparse(self.path).path
        if path == "/api/load":
            self._handle_load()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_PATCH(self) -> None:
        """PATCH 요청을 처리합니다."""
        path = urlparse(self.path).path
        if path == "/api/node":
            self._handle_node_write()
        elif path == "/api/edge":
            self._handle_edge_write()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send_threads(self) -> None:
        """스레드 목록 API 응답을 전송합니다."""
        try:
            from src.ui.graph_loader import list_threads
            threads = list_threads()
            cached = get_cached_graph_snapshot()
            cached_thread_id = str(cached.get("threadId") or "")
            if cached_thread_id and cached.get("nodes"):
                for index, thread in enumerate(threads):
                    if thread.get("id") == cached_thread_id:
                        thread["modifiedAt"] = str(cached.get("generatedAt") or thread.get("modifiedAt") or "")
                        threads.insert(0, threads.pop(index))
                        break
                else:
                    threads.insert(0, {
                        "id": cached_thread_id,
                        "name": cached_thread_id,
                        "createdAt": "",
                        "modifiedAt": str(cached.get("generatedAt") or ""),
                    })
            payload = json.dumps(threads, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def _handle_load(self) -> None:
        """요청된 스레드 DB를 그래프 스냅샷으로 로드합니다.

        DB를 먼저 읽으려 시도하고, lock 중이면 live 캐시로 폴백합니다.
        """
        try:
            body = self._read_body()
            thread_id = body.get("threadId", "")
            if not thread_id:
                self._send(400, "text/plain; charset=utf-8", b"missing threadId")
                return

            from src.ui.graph_loader import build_graph_from_thread
            try:
                graph = build_graph_from_thread(thread_id)
                payload = json.dumps(graph.model_dump(by_alias=True), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", payload)
                return
            except RuntimeError as e:
                if "Could not set lock" not in str(e):
                    raise
                # DB가 Chainlit 세션에 의해 lock 중 — live 캐시로 폴백
                live = get_cached_graph_snapshot()
                if live.get("nodes"):
                    payload = json.dumps(live, ensure_ascii=False).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", payload)
                    return
                msg = f"스레드가 Chainlit 세션에서 활성 중입니다. 자동 새로고침을 이용하세요. ({thread_id[:8]})"
                self._send(423, "text/plain; charset=utf-8", msg.encode("utf-8"))
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def _send_schema(self) -> None:
        """스레드 Kuzu DB의 테이블 스키마 목록을 반환합니다."""
        try:
            from urllib.parse import parse_qs
            params = parse_qs(urlparse(self.path).query)
            thread_id = (params.get("threadId") or [""])[0]
            if not thread_id:
                cached = get_cached_graph_snapshot()
                thread_id = str(cached.get("threadId") or "")
            if not thread_id:
                self._send(400, "text/plain; charset=utf-8", b"missing threadId")
                return
            from src.ui.graph_loader import get_thread_schema
            tables = get_thread_schema(thread_id)
            payload = json.dumps(tables, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def _read_body(self) -> dict[str, Any]:
        """요청 바디를 JSON으로 파싱합니다."""
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _handle_node_write(self) -> None:
        """노드 속성을 Kuzu DB에 반영하고 갱신된 스냅샷을 반환합니다."""
        try:
            body = self._read_body()
            thread_id = body.get("threadId", "")
            node_id = body.get("nodeId", "")
            updates = body.get("updates", {})
            if not thread_id or not node_id:
                self._send(400, "text/plain; charset=utf-8", b"missing threadId or nodeId")
                return

            from src.ui.graph_writer import write_node
            write_node(thread_id, node_id, updates)

            from src.ui.graph_loader import build_graph_from_thread
            graph = build_graph_from_thread(thread_id)
            # historical 편집 후에도 live 캐시는 덮어쓰지 않는다
            payload = json.dumps(graph.model_dump(by_alias=True), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        except Exception as exc:
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def _handle_edge_write(self) -> None:
        """엣지 속성을 Kuzu DB에 반영하고 갱신된 스냅샷을 반환합니다."""
        try:
            body = self._read_body()
            thread_id = body.get("threadId", "")
            source = body.get("source", "")
            target = body.get("target", "")
            updates = body.get("updates", {})
            if not thread_id or not source or not target:
                self._send(400, "text/plain; charset=utf-8", b"missing threadId, source, or target")
                return

            from src.ui.graph_writer import write_edge
            write_edge(thread_id, source, target, updates)

            from src.ui.graph_loader import build_graph_from_thread
            graph = build_graph_from_thread(thread_id)
            # historical 편집 후에도 live 캐시는 덮어쓰지 않는다
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
    url = f"http://{_HOST}:{_PORT}/{_DEFAULT_VIEWER}"
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


def open_in_browser() -> None:
    """기본 브라우저로 그래프 편집기를 엽니다."""
    import webbrowser
    import time
    time.sleep(0.4)
    webbrowser.open(f"http://{_HOST}:{_PORT}/{_DEFAULT_VIEWER}")


def update_graph_snapshot(graph: GraphSnapshot | dict[str, Any]) -> None:
    """최신 그래프 스냅샷을 서버 캐시에 반영합니다."""
    if isinstance(graph, GraphSnapshot):
        payload = graph.model_dump(by_alias=True)
    else:
        payload = GraphSnapshot.model_validate(graph).model_dump(by_alias=True)

    with _LOCK:
        _LATEST_GRAPH.clear()
        _LATEST_GRAPH.update(payload)


if __name__ == "__main__":
    import threading as _threading

    _SERVER = ThreadingHTTPServer((_HOST, _PORT), _GraphHandler)
    _threading.Thread(target=open_in_browser, daemon=True).start()
    print(f"Graph editor: http://{_HOST}:{_PORT}/{_DEFAULT_VIEWER}")
    print("Ctrl+C to stop.")
    try:
        _SERVER.serve_forever()
    except KeyboardInterrupt:
        pass
