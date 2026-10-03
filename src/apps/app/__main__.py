# ================================
# src/apps/app/__main__.py
#
# CLI entrypoint for the GraphRAG engine JSON API.
#
# Functions
#   - main() -> None : Parse CLI options and run the server.
# ================================

from __future__ import annotations

import argparse

from src.apps.app import run


def main() -> None:
    """Parse CLI options and run the engine JSON API server."""
    parser = argparse.ArgumentParser(description="Run the GraphRAG engine JSON API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
