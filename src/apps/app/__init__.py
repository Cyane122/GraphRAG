# ================================
# src/apps/app/__init__.py
#
# Public interface for the standalone GraphRAG web UI.
#
# Functions
#   - create_app() -> FastAPI : Create the standalone web UI app.
#   - _ensure_utf8_stdio() -> None : Force redirected console logs to UTF-8.
#   - run(host: str, port: int, open_browser: bool) -> None : Run the standalone web UI server.
# ================================

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app", "create_app", "run"]


def _ensure_utf8_stdio() -> None:
    """Force redirected stdout/stderr logs to UTF-8 when supported."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def create_app() -> "FastAPI":
    """Create the standalone web UI FastAPI app."""
    from src.apps.app.app import create_app as _create_app

    return _create_app()


def run(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    """Run the standalone web UI with uvicorn."""
    _ensure_utf8_stdio()
    # 모듈 로거(logger.warning 등)가 stderr lastResort 대신 포맷·타임스탬프·트레이스백을
    # 갖도록 루트 로거를 구성한다. uvicorn.run 이전에 호출해야 앱 로거에 핸들러가 붙는다.
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # 서드파티 라이브러리가 INFO로 쏟아내는 노이즈(모든 HTTP 요청·모델 로딩·재시도)를 억제한다.
    # 우리 코드(src.*) 로거는 루트 INFO를 그대로 유지하고, 의미 있는 폴백/경고만 콘솔에 남긴다.
    for noisy in ("httpx", "httpcore", "sentence_transformers", "transformers",
                  "huggingface_hub", "anthropic", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    import threading
    import time
    import webbrowser

    import uvicorn

    from src.apps.app.app import app

    if open_browser:
        def _open() -> None:
            """Open the web UI after the server starts."""
            time.sleep(0.8)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"GraphRAG web UI: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def __getattr__(name: str):
    """Lazily expose the app attribute."""
    if name == "app":
        from src.apps.app.app import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
