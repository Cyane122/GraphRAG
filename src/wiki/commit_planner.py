# ================================
# src/wiki/commit_planner.py
#
# Orchestrates Wiki Updater retries and returns the sole public pending-commit planning facade.
#
# Classes
#   - WikiCommitPlanningError : Imported planning failure raised when a valid proposal cannot be produced.
#
# Functions
#   - plan_pending_commit(documents: list[WikiDocument], user_input: str, actor_response: str, model_name: str, max_attempts: int = 3, player_profile_id: str = "", actor_profile_id: str = "", user_message_id: str | None = None, assistant_message_id: str | None = None, thinking_level: str | None = None, debug_root: Path | None = None) -> PendingWikiCommit : Plan a validated deferred Wiki commit with retry diagnostics.
# ================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from src.core.llm import extract_json_from_llm, get_model, get_response_text
from src.wiki.commit_errors import WikiCommitPlanningError
from src.wiki.commit_header_sync import synchronize_accepted_header
from src.wiki.commit_policy import (
    active_thread_id,
    event_titles_by_id,
    player_reference_tokens,
    validate_updater_result,
)
from src.wiki.commit_prompt import build_updater_prompt, profile_document_path
from src.wiki.document_creation import prepare_created_document
from src.wiki.models import (
    CreateMemoryDocument,
    PendingWikiCommit,
    WikiDocument,
    WikiUpdaterResult,
)
from src.wiki.updater_debug import (
    create_updater_debug_run,
    finish_updater_debug_run,
    write_updater_attempt_debug,
)

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "updater_system.md"


def _text_hash(text: str) -> str:
    """Return a stable SHA-256 hash for turn input or Actor response."""
    return sha256(text.encode("utf-8")).hexdigest()


async def plan_pending_commit(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    model_name: str,
    max_attempts: int = 3,
    player_profile_id: str = "",
    actor_profile_id: str = "",
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    thinking_level: str | None = None,
    debug_root: Path | None = None,
) -> PendingWikiCommit:
    """Validate and retry an Updater result before returning a deferred commit."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    player_document = profile_document_path(documents, player_profile_id)
    actor_document = profile_document_path(documents, actor_profile_id)
    if player_profile_id and player_document is None:
        raise WikiCommitPlanningError(f"Player character document is missing: {player_profile_id}")
    if actor_profile_id and actor_document is None:
        raise WikiCommitPlanningError(f"Actor character document is missing: {actor_profile_id}")
    documents_by_path = {document.path: document for document in documents}
    player_tokens = player_reference_tokens(
        documents_by_path.get(player_document) if player_document is not None else None
    )
    active_character_documents = {
        path for path in (player_document, actor_document) if path is not None
    }
    model = get_model(
        model_name,
        system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
    )
    prompt = build_updater_prompt(
        documents,
        user_input,
        actor_response,
        player_document,
        actor_document,
    )
    user_input_hash = _text_hash(user_input)
    actor_response_hash = _text_hash(actor_response)
    debug_run = await asyncio.to_thread(
        create_updater_debug_run,
        debug_root,
        model_name,
        max_attempts,
        user_input_hash,
        actor_response_hash,
    )
    last_error: Exception | None = None
    rejection_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        response_text = ""
        try:
            attempt_prompt = prompt
            if rejection_errors:
                rejection_history = "\n".join(
                    f"- {error}" for error in rejection_errors
                )
                attempt_prompt = "\n\n".join([
                    prompt,
                    "## Previous Attempt Rejected",
                    rejection_history,
                    (
                        "Every rejection above remains in force. Do not reintroduce a "
                        "problem fixed after an earlier attempt."
                    ),
                    "Return a corrected JSON result that satisfies every rule.",
                ])
            generation_config: dict[str, object] = {
                "temperature": 0.0,
                "max_output_tokens": 65536,
                "response_mime_type": "application/json",
                "log_source": "wiki_updater",
            }
            if thinking_level is not None:
                generation_config["thinking_config"] = {
                    "thinking_level": thinking_level
                }
            response = await model.generate_content_async(
                attempt_prompt,
                generation_config=generation_config,
            )
            response_text = get_response_text(response)
            payload = extract_json_from_llm(
                response_text,
                source="wiki_updater",
                strict=True,
            )
            result = WikiUpdaterResult.model_validate(payload)
            validate_updater_result(
                result,
                documents,
                user_input=user_input,
                actor_response=actor_response,
                player_document=player_document,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
                player_reference_tokens=player_tokens,
                active_character_documents=active_character_documents,
            )
            synchronize_accepted_header(
                result,
                documents,
                user_input,
                actor_response,
            )
            await asyncio.to_thread(
                write_updater_attempt_debug,
                debug_run,
                attempt,
                attempt_prompt,
                response_text,
                None,
            )
            await asyncio.to_thread(
                finish_updater_debug_run,
                debug_run,
                "accepted",
                attempt,
            )
            commit_id = uuid4().hex
            created_at = datetime.now(timezone.utc)
            thread_id = active_thread_id(documents)
            event_titles = event_titles_by_id(documents, result.creations)
            creations = [
                prepare_created_document(
                    creation,
                    thread_id,
                    commit_id,
                    created_at,
                    user_message_id,
                    assistant_message_id,
                    (
                        event_titles[creation.related_event_id]
                        if isinstance(creation, CreateMemoryDocument)
                        else None
                    ),
                )
                for creation in result.creations
            ]
            return PendingWikiCommit(
                commit_id=commit_id,
                created_at=created_at,
                user_input_hash=user_input_hash,
                actor_response_hash=actor_response_hash,
                updater_model=model_name,
                updater_attempts=attempt,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                summary=result.summary,
                patches=result.patches,
                creations=creations,
            )
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            if error_text not in rejection_errors:
                rejection_errors.append(error_text)
            await asyncio.to_thread(
                write_updater_attempt_debug,
                debug_run,
                attempt,
                attempt_prompt,
                response_text,
                str(exc),
            )
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))

    final_error = f"Wiki updater failed after {max_attempts} attempts: {last_error}"
    await asyncio.to_thread(
        finish_updater_debug_run,
        debug_run,
        "failed",
        max_attempts,
        final_error,
    )
    raise WikiCommitPlanningError(final_error) from last_error
