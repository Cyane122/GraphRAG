# ================================
# scripts/run_wiki_llm_validation.py
#
# 실제 LLM으로 Wiki 한 턴의 Actor·Updater·deferred commit lifecycle을 임시 vault에서 검증합니다.
#
# Functions
#   - _patch_vault_root(vault_root: Path) -> None : 앱 모듈의 Wiki vault 참조를 임시 검증 경로로 맞춥니다.
#   - _canonical_documents(thread_root: Path) -> dict[str, str] : runtime 산출물을 제외한 canonical Markdown snapshot을 반환합니다.
#   - _render_document_diff(before: dict[str, str], after: dict[str, str]) -> str : 문서 snapshot 사이의 unified diff를 렌더링합니다.
#   - _write_json(path: Path, payload: object) -> None : UTF-8 JSON artifact를 저장합니다.
#   - _run_validation(scenario_id: str, user_input: str, actor_model: str | None, output_root: Path) -> Path : 실제 Wiki 한 턴을 실행하고 artifact 경로를 반환합니다.
#   - _parse_args() -> argparse.Namespace : CLI 인자를 파싱합니다.
#   - main() -> None : 검증을 실행하고 결과 경로를 출력합니다.
# ================================

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.apps.app.conversation_lifecycle as conversation_lifecycle
import src.apps.app.runtime as app_runtime
import src.apps.app.service as app_service
import src.apps.app.wiki_branching as wiki_branching
import src.apps.app.wiki_controls as wiki_controls
import src.apps.app.wiki_service as wiki_service
from src.apps.app.storage import ConversationStore


_RUNTIME_MARKDOWN_PARTS = frozenset({"commits", "debug"})


def _patch_vault_root(vault_root: Path) -> None:
    """Point app modules with imported Wiki roots at the isolated validation vault."""
    app_runtime.WIKI_VAULT_ROOT = vault_root
    app_service.WIKI_VAULT_ROOT = vault_root
    conversation_lifecycle.WIKI_VAULT_ROOT = vault_root
    wiki_branching.WIKI_VAULT_ROOT = vault_root
    wiki_controls.WIKI_VAULT_ROOT = vault_root
    wiki_service.WIKI_VAULT_ROOT = vault_root


def _canonical_documents(thread_root: Path) -> dict[str, str]:
    """Return canonical Markdown content keyed by thread-relative path."""
    documents: dict[str, str] = {}
    for path in sorted(thread_root.rglob("*.md")):
        relative = path.relative_to(thread_root)
        if path.name == "commit.md" or _RUNTIME_MARKDOWN_PARTS & set(relative.parts):
            continue
        documents[relative.as_posix()] = path.read_text(encoding="utf-8")
    return documents


def _render_document_diff(
    before: dict[str, str],
    after: dict[str, str],
) -> str:
    """Render unified diffs for every created, deleted, or changed canonical document."""
    chunks: list[str] = []
    for document in sorted(set(before) | set(after)):
        previous = before.get(document, "")
        current = after.get(document, "")
        if previous == current:
            continue
        chunks.extend(
            difflib.unified_diff(
                previous.splitlines(),
                current.splitlines(),
                fromfile=f"before/{document}",
                tofile=f"after/{document}",
                lineterm="",
            )
        )
    return "\n".join(chunks) + ("\n" if chunks else "")


def _write_json(path: Path, payload: object) -> None:
    """Write one JSON artifact as readable UTF-8 text."""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
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
        _patch_vault_root(vault_root)
        store = ConversationStore(temporary_root / "data" / "threads")
        state = app_service.create_conversation(
            "babe_university",
            scenario_id,
            store,
            actor_model=actor_model,
            world_mode="wiki",
        )
        thread_root = vault_root / "threads" / state.thread_id
        before_generation = _canonical_documents(thread_root)

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

        before_apply = _canonical_documents(thread_root)
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
        after_apply = _canonical_documents(thread_root)
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
        _write_json(run_root / "result.json", result)
        _write_json(run_root / "events.json", events)
        (run_root / "pending_commit.md").write_text(
            pending_text,
            encoding="utf-8",
        )
        (run_root / "canonical.diff").write_text(
            _render_document_diff(before_apply, after_apply),
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
