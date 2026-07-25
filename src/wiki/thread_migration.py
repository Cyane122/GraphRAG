# ================================
# src/wiki/thread_migration.py
#
# 기존 Wiki thread의 런타임 소유 캐릭터 상태 섹션을 명시적으로 보강합니다.
#
# Functions
#   - plan_thread_contract_migration(vault_root: Path, thread_id: str) -> WikiThreadMigrationPlan : 쓰기 없는 상태 계약 migration 계획을 만듭니다.
#   - apply_thread_contract_migration(vault_root: Path, thread_id: str) -> WikiThreadMigrationPlan : 계획을 audited manual commit으로 즉시 적용합니다.
# ================================

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from src.wiki.commit import WikiCommitQueue
from src.wiki.context import read_wiki_thread_documents
from src.wiki.markdown import document_revision, parse_markdown_sections
from src.wiki.models import (
    PendingWikiCommit,
    SectionPatch,
    WikiDocument,
    WikiThreadMigrationPlan,
)
from src.wiki.store import WikiStore


_NEEDS_SECTION = """### 욕구와 컨디션

- Needs: hunger=0.3000; rest=0.2000; social=0.1000; fun=0.4000; safety=0.0500; libido=0.2000
- Active pressure: none
- Condition: stable"""

_PERSONALITY_SECTION = """### Personality Change Ledger

- No durable personality change has occurred since the story began."""

_REPRODUCTIVE_SECTION = """### Reproductive State

- Menstrual cycle: disabled
- Cycle day: 1
- Pregnant: no
- Pregnancy day: 0
- Internal ejaculation count this cycle: 0
- Other parent: unknown"""

_REQUIRED_SECTIONS = (
    ("욕구와 컨디션", _NEEDS_SECTION),
    ("Personality Change Ledger", _PERSONALITY_SECTION),
    ("Reproductive State", _REPRODUCTIVE_SECTION),
)


def _migration_patch(document: WikiDocument) -> SectionPatch | None:
    """캐릭터의 누락된 런타임 H3를 보강하는 complete 현재 상태 H2 patch를 반환합니다."""
    sections = parse_markdown_sections(document.content)
    current = sections.get(("현재 상태",))
    if current is None:
        return None
    missing = [
        markdown
        for title, markdown in _REQUIRED_SECTIONS
        if ("현재 상태", title) not in sections
    ]
    if not missing:
        return None
    replacement = current.markdown.rstrip() + "\n\n" + "\n\n".join(missing)
    return SectionPatch(
        document=document.path,
        base_revision=document.revision,
        base_section_revision=document_revision(current.markdown),
        base_markdown=current.markdown,
        section_path=("현재 상태",),
        replacement_markdown=replacement,
        evidence="Explicit player-approved Wiki thread state-contract migration",
        evidence_source="player_input",
        confidence=1.0,
    )


def plan_thread_contract_migration(
    vault_root: Path,
    thread_id: str,
) -> WikiThreadMigrationPlan:
    """기존 캐릭터 문서를 읽고 원문을 쓰지 않는 migration 미리보기를 반환합니다."""
    documents = read_wiki_thread_documents(vault_root, thread_id)
    thread_root = vault_root.resolve() / "threads" / thread_id
    queue = WikiCommitQueue(WikiStore(thread_root))
    if queue.load() is not None:
        return WikiThreadMigrationPlan(
            status="conflict",
            message="Apply, retry, or skip the existing commit.md before migration.",
        )

    characters = [
        document
        for document in documents
        if document.metadata is not None and document.metadata.type == "character"
    ]
    missing_current_state = [
        document.path
        for document in characters
        if ("현재 상태",) not in parse_markdown_sections(document.content)
    ]
    if missing_current_state:
        joined = ", ".join(missing_current_state)
        return WikiThreadMigrationPlan(
            status="conflict",
            message=f"Character documents have no complete '현재 상태' H2: {joined}",
            changed_documents=missing_current_state,
        )

    patches = [
        patch
        for document in characters
        if (patch := _migration_patch(document)) is not None
    ]
    if not patches:
        return WikiThreadMigrationPlan(
            status="up_to_date",
            message="All character runtime state sections are already present.",
        )
    return WikiThreadMigrationPlan(
        status="ready",
        message="The migration is ready and has not changed canonical Markdown.",
        changed_documents=[patch.document for patch in patches],
        patches=patches,
    )


def apply_thread_contract_migration(
    vault_root: Path,
    thread_id: str,
) -> WikiThreadMigrationPlan:
    """최신 migration 계획을 audited manual commit으로 즉시 적용합니다."""
    plan = plan_thread_contract_migration(vault_root, thread_id)
    if plan.status != "ready":
        return plan

    source = f"wiki-thread-contract-migration:{thread_id}"
    pending = PendingWikiCommit(
        user_input_hash=sha256(source.encode("utf-8")).hexdigest(),
        actor_response_hash=sha256(f"{source}:deterministic".encode("utf-8")).hexdigest(),
        updater_model="deterministic-thread-contract-migration",
        operation="manual",
        summary="Added missing runtime-owned character state sections.",
        patches=plan.patches,
    )
    thread_root = vault_root.resolve() / "threads" / thread_id
    applied = WikiCommitQueue(WikiStore(thread_root)).apply_immediate(pending)
    return plan.model_copy(
        update={
            "status": "applied",
            "message": "The migration was applied as an audited manual commit.",
            "migration_commit_id": applied.commit_id,
        }
    )
