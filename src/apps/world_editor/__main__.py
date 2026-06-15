# ================================
# src/apps/world_editor/__main__.py
#
# 세계관 수정기 백엔드 CLI 엔트리포인트를 제공합니다.
#
# Functions
#   - main() -> None : 세계관 수정기 서버를 실행합니다.
# ================================

from __future__ import annotations

from src.apps.world_editor import run


def main() -> None:
    """세계관 수정기 서버를 실행합니다."""
    run()


if __name__ == "__main__":
    main()
