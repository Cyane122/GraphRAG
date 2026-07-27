# ================================
# scripts/run_wiki_llm_validation.py
#
# 실제 LLM으로 Wiki 한 턴의 Actor·Updater·deferred commit lifecycle을 임시 vault에서 검증합니다.
#
# Functions
#   - _run_validation(scenario_id: str, user_input: str, actor_model: str | None, output_root: Path) -> Path : 실제 Wiki 한 턴을 실행하고 artifact 경로를 반환합니다.
#   - _parse_args() -> argparse.Namespace : CLI 인자를 파싱합니다.
#   - main() -> None : 검증을 실행하고 결과 경로를 출력합니다.
# ================================

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.apps.app.service as app_service
import src.apps.app.wiki_controls as wiki_controls
from src.apps.app.storage import ConversationStore
from scripts.wiki_validation_common import (
    canonical_documents,
    patch_vault_root,
    render_document_diff,
    write_json,
)


async def _run_validation(
    scenario_id: str,
    user_input: str,
    actor_model: str | None,
    output_root: Path,
) -> Path:
    """Run one real Wiki turn in an isolated vault and preserve its evidence artifacts."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run_root = output_root / f"{stamp}_{scenario_id}"
    run_root.mkdir(parents=True, exist_ok=False)

    with tempfile.TemporaryDirectory(prefix="wiki_llm_validation_") as temporary:
        temporary_root = Path(temporary)
        vault_root = temporary_root / "wiki_v2"
        source_world = Path("wiki_v2/worlds/babe_university")
        shutil.copytree(
            source_world,
            vault_root / "worlds" / "babe_university",
        )
        patch_vault_root(vault_root)
        store = ConversationStore(temporary_root / "data" / "threads")
        state = app_service.create_conversation(
            "babe_university",
            scenario_id,
            store,
            actor_model=actor_model,
            world_mode="wiki",
        )
        thread_root = vault_root / "threads" / state.thread_id
        before_generation = canonical_documents(thread_root)

        started_at = datetime.now(timezone.utc)
        events: list[dict] = []
        async for event in app_service.append_user_and_stream(
            state,
            user_input,
            store,
            actor_model=actor_model,
        ):
            if event.get("type") != "token":
                events.append(event)
        finished_generation_at = datetime.now(timezone.utc)

        before_apply = canonical_documents(thread_root)
        pending_path = thread_root / "commit.md"
        pending_text = (
            pending_path.read_text(encoding="utf-8")
            if pending_path.is_file()
            else ""
        )
        pending_queued = (
            state.wiki_update_status == "queued"
            and bool(state.wiki_pending_commit_id)
            and pending_path.is_file()
        )
        pending_commit_id = state.wiki_pending_commit_id
        update_status_before_apply = state.wiki_update_status
        canonical_unchanged_before_apply = before_generation == before_apply

        apply_status: dict | None = None
        apply_error = ""
        if pending_queued:
            try:
                apply_status = wiki_controls.apply_wiki_commit_now(
                    state,
                    store,
                ).model_dump(mode="json")
            except Exception as exc:
                apply_error = str(exc)
        after_apply = canonical_documents(thread_root)
        finished_at = datetime.now(timezone.utc)

        assistant = next(
            (
                message
                for message in reversed(state.messages)
                if message.role == "assistant"
            ),
            None,
        )
        changed_documents = sorted(
            document
            for document in set(before_apply) | set(after_apply)
            if before_apply.get(document) != after_apply.get(document)
        )
        checks = {
            "canonical_unchanged_before_apply": canonical_unchanged_before_apply,
            "pending_commit_created": pending_queued,
            "apply_succeeded": bool(
                apply_status
                and apply_status.get("update_status") == "applied"
                and not apply_error
            ),
            "canonical_documents_changed_after_apply": changed_documents,
        }
        result = {
            "world_id": state.world_id,
            "scenario_id": scenario_id,
            "thread_id": state.thread_id,
            "actor_model": state.actor_model,
            "user_input": user_input,
            "actor_response": assistant.content if assistant is not None else "",
            "wiki_update_status_before_apply": update_status_before_apply,
            "wiki_update_error": state.wiki_update_error,
            "pending_commit_id": pending_commit_id,
            "apply_status": apply_status,
            "apply_error": apply_error,
            "checks": checks,
            "timing_seconds": {
                "generation": (
                    finished_generation_at - started_at
                ).total_seconds(),
                "total": (finished_at - started_at).total_seconds(),
            },
        }
        write_json(run_root / "result.json", result)
        write_json(run_root / "events.json", events)
        (run_root / "pending_commit.md").write_text(
            pending_text,
            encoding="utf-8",
        )
        (run_root / "canonical.diff").write_text(
            render_document_diff(before_apply, after_apply),
            encoding="utf-8",
        )
        shutil.copytree(thread_root, run_root / "thread_snapshot")

        summary = [
            f"# Wiki LLM Validation — {scenario_id}",
            "",
            f"- actor model: `{state.actor_model}`",
            f"- generation seconds: `{result['timing_seconds']['generation']:.3f}`",
            f"- deferred canonical unchanged: `{canonical_unchanged_before_apply}`",
            f"- pending commit created: `{pending_queued}`",
            f"- apply succeeded: `{checks['apply_succeeded']}`",
            f"- changed canonical documents: `{changed_documents}`",
            f"- updater error: `{state.wiki_update_error or apply_error}`",
            "",
            "## User Input",
            "",
            user_input,
            "",
            "## Actor Response",
            "",
            assistant.content if assistant is not None else "",
        ]
        (run_root / "summary.md").write_text(
            "\n".join(summary) + "\n",
            encoding="utf-8",
        )
    return run_root


def _parse_args() -> argparse.Namespace:
    """Parse scenario, turn input, model override, and artifact destination."""
    parser = argparse.ArgumentParser(
        description="Run one real Wiki Actor/Updater/deferred-commit validation turn.",
    )
    parser.add_argument("--scenario", default="lover")
    parser.add_argument("--user-input", required=True)
    parser.add_argument("--actor-model")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("docs/wiki_llm_runs"),
    )
    return parser.parse_args()


def main() -> None:
    """Run the requested real-LLM validation and print its artifact directory."""
    args = _parse_args()
    run_root = asyncio.run(
        _run_validation(
            scenario_id=args.scenario,
            user_input=args.user_input,
            actor_model=args.actor_model,
            output_root=args.output_root,
        )
    )
    print(run_root)


if __name__ == "__main__":
    main()
