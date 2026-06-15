# ================================
# src/ui/web_app/__main__.py
#
# CLI entrypoint for the standalone GraphRAG web UI.
#
# Functions
#   - main() -> None : Parse CLI options and run the server.
# ================================

from __future__ import annotations

import argparse

from src.ui.web_app import run


def main() -> None:
    """Parse CLI options and run the standalone web UI server."""
    parser = argparse.ArgumentParser(description="Run the standalone GraphRAG web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    run(host=args.host, port=args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    main()
