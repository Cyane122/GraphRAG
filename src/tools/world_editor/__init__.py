# ================================
# src/tools/world_editor/__init__.py
#
# world_editor 패키지의 공개 인터페이스.
# 세계관 저작(프롬프트 .md 편집 + 스키마 읽기 + 데이터 .py AST 쓰기)을 위한 FastAPI 도구.
# app/FastAPI는 지연 import 한다 — worlds/compiler/prompts 단독 사용 시 무거운 체인을 끌어오지 않도록.
#
# Functions
#   - create_app() -> FastAPI : 라우트가 등록된 앱 인스턴스 생성 (지연 import)
#   - run(host: str, port: int, open_browser: bool) -> None : uvicorn 서버 실행 + 브라우저 오픈
#   - __getattr__(name) : `app` 속성 지연 접근 (PEP 562)
# ================================

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["app", "create_app", "run"]


def create_app() -> "FastAPI":
    """라우트가 등록된 FastAPI 앱 인스턴스를 생성합니다 (app 모듈 지연 import)."""
    from src.tools.world_editor.app import create_app as _factory
    return _factory()


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """uvicorn으로 편집기 서버를 띄우고, 선택 시 기본 브라우저를 엽니다."""
    import threading
    import time
    import webbrowser

    import uvicorn

    from src.tools.world_editor.app import app

    # 서버가 뜰 시간을 잠깐 준 뒤 브라우저 오픈 (데몬 스레드)
    if open_browser:
        def _open() -> None:
            """서버 기동 직후 브라우저로 편집기 페이지를 엽니다."""
            time.sleep(0.8)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"World editor: http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="info")


def __getattr__(name: str):
    """`app` 속성을 지연 import로 노출합니다 (uvicorn/외부 참조용)."""
    if name == "app":
        from src.tools.world_editor.app import app
        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
