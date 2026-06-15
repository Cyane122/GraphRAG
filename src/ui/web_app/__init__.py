# ================================
# src/ui/web_app/__init__.py
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
    from src.ui.web_app.app import create_app as _create_app

    return _create_app()


def run(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    """Run the standalone web UI with uvicorn."""
    _ensure_utf8_stdio()
    import threading
    import time
    import webbrowser

    import uvicorn

    from src.ui.web_app.app import app

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
        from src.ui.web_app.app import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
