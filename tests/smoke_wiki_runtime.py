# ================================
# tests/smoke_wiki_runtime.py
#
# Wiki runtime smoke runner orchestrates the split prompt, flow, and branching suites while keeping the historical entrypoint.
#
# Functions
#   - _run() -> None : Run the full split Wiki runtime smoke suite.
#   - main() -> None : Run the standalone runtime smoke suite.
# ================================

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.smoke_wiki_runtime_branching import run_runtime_branching_suite  # noqa: E402
from tests.smoke_wiki_runtime_flow import run_runtime_flow_suite  # noqa: E402
from tests.smoke_wiki_runtime_prompt import run_runtime_prompt_suite  # noqa: E402
from tests.wiki_runtime_smoke_fixtures import (  # noqa: E402
    configure_runtime_environment,
    copy_runtime_world,
)

async def _run() -> None:
    """Run the full split Wiki runtime smoke suite."""
    with TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        vault_root = copy_runtime_world(temporary_root)
        configure_runtime_environment(temporary_root, vault_root)
        await run_runtime_prompt_suite(temporary_root, vault_root)
        handles = await run_runtime_flow_suite(temporary_root, vault_root)
        await run_runtime_branching_suite(vault_root, handles)

def main() -> None:
    """Run the standalone runtime smoke suite."""
    asyncio.run(_run())
    print("smoke_wiki_runtime: ok")

if __name__ == "__main__":
    main()
