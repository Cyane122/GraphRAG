# ================================
# src/apps/app/routers/wiki.py
#
# Wiki conversation lifecycle, commit, audit, migration, and branching routes.
#
# Functions
#   - create_router(context: RouterContext) -> APIRouter : Register Wiki routes
# ================================

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from src.apps.app.conversation_lifecycle import (
    delete_wiki_conversation,
    export_wiki_conversation,
    rename_wiki_conversation,
    set_wiki_conversation_archived,
)
from src.apps.app.models import (
    WikiCommitSkipRequest,
    WikiConversationArchiveRequest,
    WikiConversationRenameRequest,
    WikiSystemsPatchRequest,
)
from src.apps.app.routers.shared import (
    RouterContext,
    _conversation_payload,
    _load_or_404,
    _require_wiki_mode,
)
from src.apps.app.wiki_branching import branch_wiki_conversation_before_message
from src.apps.app.wiki_controls import (
    apply_wiki_commit_inverse,
    apply_wiki_commit_now,
    apply_wiki_thread_migration,
    get_wiki_commit_status,
    get_wiki_diagnostics,
    get_wiki_document_list,
    get_wiki_manual_audit,
    get_wiki_systems,
    get_wiki_thread_migration,
    plan_wiki_commit_inverse,
    record_wiki_manual_audit,
    regenerate_wiki_update,
    retry_wiki_update,
    skip_wiki_commit,
    update_wiki_systems,
)
from src.wiki.paths import WikiContextError


def create_router(context: RouterContext) -> APIRouter:
    """Register Wiki conversation lifecycle and control routes."""
    router = APIRouter()
    store = context.store

    @router.patch("/api/conversations/{thread_id}/wiki/title")
    def api_rename_wiki_conversation(
        thread_id: str,
        body: WikiConversationRenameRequest,
    ) -> dict:
        """Change one Wiki conversation's user-facing title."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            renamed = rename_wiki_conversation(state, body.title, store)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _conversation_payload(renamed)

    @router.patch("/api/conversations/{thread_id}/wiki/archive")
    def api_archive_wiki_conversation(
        thread_id: str,
        body: WikiConversationArchiveRequest,
    ) -> dict:
        """Archive or restore one Wiki conversation."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            archived = set_wiki_conversation_archived(
                state,
                body.archived,
                store,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return _conversation_payload(archived)

    @router.get("/api/conversations/{thread_id}/wiki/export")
    def api_export_wiki_conversation(thread_id: str) -> Response:
        """Download one Wiki conversation and its canonical Markdown as ZIP."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            content, filename = export_wiki_conversation(state)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail="Wiki thread vault not found") from exc
        except (ValueError, WikiContextError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @router.delete("/api/conversations/{thread_id}/wiki")
    def api_delete_wiki_conversation(thread_id: str) -> dict:
        """Permanently delete one Wiki conversation and its thread vault."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            delete_wiki_conversation(state, store)
        except (FileNotFoundError, OSError, ValueError, WikiContextError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"deleted": True, "thread_id": thread_id}

    @router.get("/api/conversations/{thread_id}/wiki/commit")
    def api_wiki_commit_status(thread_id: str) -> dict:
        """Return the current Wiki updater and deferred commit state."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_commit_status(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.get("/api/conversations/{thread_id}/wiki/diagnostics")
    def api_wiki_diagnostics(thread_id: str) -> dict:
        """Return vault integrity diagnostics for the conversation's Wiki scope."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        return {
            "diagnostics": [
                diagnostic.model_dump(mode="json")
                for diagnostic in get_wiki_diagnostics(state)
            ]
        }

    @router.get("/api/conversations/{thread_id}/wiki/documents")
    def api_wiki_documents(thread_id: str) -> dict:
        """Return the Explorer document summary list for the conversation's Wiki scope."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        return {
            "documents": [
                summary.model_dump(mode="json")
                for summary in get_wiki_document_list(state)
            ]
        }

    @router.get("/api/conversations/{thread_id}/wiki/systems")
    def api_wiki_systems(thread_id: str) -> dict:
        """Return effective Wiki system toggles and authored cycle-ready characters."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        return get_wiki_systems(state).model_dump(mode="json")

    @router.patch("/api/conversations/{thread_id}/wiki/systems")
    def api_update_wiki_systems(
        thread_id: str,
        body: WikiSystemsPatchRequest,
    ) -> dict:
        """Update per-conversation Wiki system overrides and return the resolved state."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return update_wiki_systems(state, store, body.systems).model_dump(
                mode="json"
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @router.get("/api/conversations/{thread_id}/wiki/migration")
    def api_wiki_thread_migration(thread_id: str) -> dict:
        """Return a write-free runtime state-contract migration preview."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_thread_migration(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.get("/api/conversations/{thread_id}/wiki/manual-audit")
    def api_wiki_manual_audit(thread_id: str) -> dict:
        """Preview external canonical Markdown changes without writing an archive."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return get_wiki_manual_audit(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.post("/api/conversations/{thread_id}/wiki/manual-audit/record")
    def api_record_wiki_manual_audit(thread_id: str) -> dict:
        """Record external canonical Markdown changes as an applied manual commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return record_wiki_manual_audit(state).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.post("/api/conversations/{thread_id}/wiki/migration/apply")
    def api_apply_wiki_thread_migration(thread_id: str) -> dict:
        """Apply the current runtime state contract as an audited manual commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return apply_wiki_thread_migration(state, store).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.post("/api/conversations/{thread_id}/wiki/commit/apply")
    def api_apply_wiki_commit(thread_id: str) -> dict:
        """Apply the current Wiki commit.md before the next user message."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            return apply_wiki_commit_now(state, store).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @router.post("/api/conversations/{thread_id}/wiki/commit/retry")
    async def api_retry_wiki_update(thread_id: str) -> dict:
        """Retry the Wiki updater using the latest accepted turn pair."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = await retry_wiki_update(state, store)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/api/conversations/{thread_id}/wiki/commit/regenerate")
    async def api_regenerate_wiki_update(thread_id: str) -> dict:
        """Create a fresh Wiki commit from the latest accepted turn pair."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = await regenerate_wiki_update(state, store)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/api/conversations/{thread_id}/wiki/commit/skip")
    def api_skip_wiki_commit(
        thread_id: str,
        body: WikiCommitSkipRequest | None = Body(default=None),
    ) -> dict:
        """Archive the current Wiki commit without applying its patches."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = skip_wiki_commit(state, store, body.reason if body else "")
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.get("/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse")
    def api_plan_wiki_commit_inverse(thread_id: str, commit_id: str) -> dict:
        """Return a write-free inverse plan for one applied Wiki commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = plan_wiki_commit_inverse(state, commit_id)
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/api/conversations/{thread_id}/wiki/commits/{commit_id}/inverse/apply")
    def api_apply_wiki_commit_inverse(thread_id: str, commit_id: str) -> dict:
        """Apply one conflict-free inverse as a new audited Wiki commit."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = apply_wiki_commit_inverse(state, store, commit_id)
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post("/api/conversations/{thread_id}/wiki/branch/{message_id}")
    def api_branch_wiki_conversation(
        thread_id: str,
        message_id: str,
    ) -> dict:
        """Create a new Wiki conversation immediately before the selected turn."""
        state = _load_or_404(store, thread_id)
        _require_wiki_mode(state)
        try:
            result = branch_wiki_conversation_before_message(
                state,
                message_id,
                store,
            )
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {
            **_conversation_payload(result.conversation),
            "draft": result.draft,
            "source_thread_id": result.source_thread_id,
            "source_user_message_id": result.source_user_message_id,
        }

    return router
