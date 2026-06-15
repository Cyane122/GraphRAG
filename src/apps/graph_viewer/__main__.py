# ================================
# src/apps/graph_viewer/__main__.py
#
# 그래프 뷰어 백엔드 CLI 엔트리포인트를 제공합니다.
#
# Functions
#   - main() -> None : 그래프 뷰어 서버를 실행합니다.
# ================================

from __future__ import annotations

from src.apps.graph_viewer import run


def main() -> None:
    """그래프 뷰어 서버를 실행합니다."""
    run()


if __name__ == "__main__":
    main()
