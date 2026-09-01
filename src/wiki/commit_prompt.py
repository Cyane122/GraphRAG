# ================================
# src/wiki/commit_prompt.py
#
# Assembles the Wiki Updater prompt and resolves active character documents.
#
# Functions
#   - build_updater_prompt(documents: list[WikiDocument], user_input: str, actor_response: str, player_document: str | None, actor_document: str | None) -> str : Assemble the complete Wiki Updater request.
#   - profile_document_path(documents: list[WikiDocument], profile_id: str) -> str | None : Return the character document path for an active profile ID.
# ================================

from __future__ import annotations

from pathlib import Path

from src.wiki.models import WikiDocument

_PROMPT_PATH = Path(__file__).parent / "prompts" / "updater.md"


def build_updater_prompt(
    documents: list[WikiDocument],
    user_input: str,
    actor_response: str,
    player_document: str | None,
    actor_document: str | None,
) -> str:
    """Assemble the Updater rules, authority, turn prose, and documents."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    document_blocks = []
    for document in documents:
        document_blocks.append(
            "\n".join([
                f'<wiki_document path="{document.path}" revision="{document.revision}">',
                document.content,
                "</wiki_document>",
            ])
        )
    return "\n\n".join([
        template.strip(),
        "## Update Authority",
        f"- Player character document: {player_document or 'none'}",
        f"- Actor character document: {actor_document or 'none'}",
        "## Player Input",
        user_input,
        "## Accepted Actor Response",
        actor_response,
        "## Current Wiki Documents",
        "\n\n".join(document_blocks),
    ])


def profile_document_path(
    documents: list[WikiDocument],
    profile_id: str,
) -> str | None:
    """Return the thread character document path for a profile ID."""
    if not profile_id:
        return None
    for document in documents:
        metadata = document.metadata
        if (
            metadata is not None
            and metadata.type == "character"
            and metadata.profile_id == profile_id
        ):
            return document.path
    return None
