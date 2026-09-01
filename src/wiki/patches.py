# ================================
# src/wiki/patches.py
#
# Builds revision-safe actor-response section patches from canonical Wiki documents.
#
# Functions
#   - build_actor_response_section_patch(document: WikiDocument, section_path: tuple[str, ...], replacement_markdown: str, evidence: str) -> SectionPatch | None : Build an actor-response patch for an existing section.
# ================================

from __future__ import annotations

from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import SectionPatch, WikiDocument


def build_actor_response_section_patch(
    document: WikiDocument,
    section_path: tuple[str, ...],
    replacement_markdown: str,
    evidence: str,
) -> SectionPatch | None:
    """Build an actor-response patch when the requested document section exists."""
    section = parse_markdown_sections(document.content).get(section_path)
    if section is None or not evidence:
        return None
    return SectionPatch(
        document=document.path,
        base_revision=document.revision,
        base_section_revision=document_revision(section.markdown),
        base_markdown=section.markdown,
        section_path=section_path,
        replacement_markdown=replacement_markdown,
        evidence=evidence,
        evidence_source="actor_response",
        confidence=1.0,
    )
