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
#   - _reject_attempt(debug_run: Path | None, attempt: int, max_attempts: int, attempt_prompt: str, response_text: str, exc: Exception, rejection_errors: list[str]) -> None : Record one rejected Updater attempt and back off before the next retry.
# ================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from src.core.llm import extract_json_from_llm, get_model, get_response_text
from src.core.llm.errors import LLMJsonError
from src.wiki.commit_errors import WikiCommitPlanningError
from src.wiki.commit_header_sync import synchronize_accepted_header
from src.wiki.commit_policy import (
    player_reference_tokens,
    scene_active_profile_ids,
    validate_updater_result,
)
from src.wiki.commit_prompt import build_updater_prompt, profile_document_path
from src.wiki.markdown import MarkdownStructureError
from src.wiki.models import (
    DocumentCreation,
    PendingWikiCommit,
    SeveredCreation,
    WikiDocument,
    WikiUpdaterResult,
)
from src.wiki.prompt_contract import WikiPromptContractError
from src.wiki.updater_debug import (
    create_updater_debug_run,
    finish_updater_debug_run,
    write_updater_attempt_debug,
    write_updater_attempt_severed,
)

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "updater_system.md"


def _text_hash(text: str) -> str:
    """Return a stable SHA-256 hash for turn input or Actor response."""
    return sha256(text.encode("utf-8")).hexdigest()


async def _reject_attempt(
    debug_run: Path | None,
    attempt: int,
    max_attempts: int,
    attempt_prompt: str,
    response_text: str,
    exc: Exception,
    rejection_errors: list[str],
) -> None:
    """Record one rejected Updater attempt and back off before the next retry.

    Shared by both retry boundaries (model call and parse/validate/assemble) so a
    rejection is recorded identically regardless of which boundary raised it.
    """
    error_text = str(exc)
    if error_text not in rejection_errors:
        rejection_errors.append(error_text)
    await asyncio.to_thread(
        write_updater_attempt_debug,
        debug_run,
        attempt,
        attempt_prompt,
        response_text,
        error_text,
    )
    if attempt < max_attempts:
        await asyncio.sleep(min(2 ** (attempt - 1), 4))


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
    active_scene_profile_ids = scene_active_profile_ids(documents)
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

        # Outer boundary: the model call itself. This is a genuine external
        # boundary (network, provider SDK), so it stays broad on purpose —
        # the block is narrow enough that none of our own programming errors
        # can originate inside it.
        try:
            response = await model.generate_content_async(
                attempt_prompt,
                generation_config=generation_config,
            )
            response_text = get_response_text(response)
        except Exception as exc:
            last_error = exc
            await _reject_attempt(
                debug_run,
                attempt,
                max_attempts,
                attempt_prompt,
                response_text,
                exc,
                rejection_errors,
            )
            continue

        # Inner boundary: parsing, schema validation, and policy/assembly.
        # Only expected rejection types are caught here — a programming error
        # in our own validation code (KeyError, AttributeError, TypeError, ...)
        # must propagate immediately instead of being spent as a retry.
        try:
            payload = extract_json_from_llm(
                response_text,
                source="wiki_updater",
                strict=True,
            )
            result = WikiUpdaterResult.model_validate(payload)
            commit_id = uuid4().hex
            created_at = datetime.now(timezone.utc)
            creations: list[DocumentCreation]
            severed_creations: list[SeveredCreation]
            creations, severed_creations = validate_updater_result(
                result,
                documents,
                user_input=user_input,
                actor_response=actor_response,
                player_document=player_document,
                player_profile_id=player_profile_id,
                actor_profile_id=actor_profile_id,
                player_reference_tokens=player_tokens,
                active_character_documents=active_character_documents,
                scene_active_profile_ids=active_scene_profile_ids,
                commit_id=commit_id,
                created_at=created_at,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            synchronize_accepted_header(
                result,
                documents,
                user_input,
                actor_response,
            )
        except (
            WikiCommitPlanningError,
            ValidationError,
            LLMJsonError,
            WikiPromptContractError,
            MarkdownStructureError,
            asyncio.TimeoutError,
        ) as exc:
            last_error = exc
            await _reject_attempt(
                debug_run,
                attempt,
                max_attempts,
                attempt_prompt,
                response_text,
                exc,
                rejection_errors,
            )
            continue

        await asyncio.to_thread(
            write_updater_attempt_debug,
            debug_run,
            attempt,
            attempt_prompt,
            response_text,
            None,
        )
        # Severance is a completed action, not a rejection, so it is recorded
        # in its own diagnostics file and never folded into `rejection_errors`
        # — the correction prompt above must stay free of severed reasons.
        await asyncio.to_thread(
            write_updater_attempt_severed,
            debug_run,
            attempt,
            severed_creations,
        )
        await asyncio.to_thread(
            finish_updater_debug_run,
            debug_run,
            "accepted",
            attempt,
        )
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
            severed_creations=severed_creations,
        )

    final_error = f"Wiki updater failed after {max_attempts} attempts: {last_error}"
    await asyncio.to_thread(
        finish_updater_debug_run,
        debug_run,
        "failed",
        max_attempts,
        final_error,
    )
    raise WikiCommitPlanningError(final_error) from last_error
