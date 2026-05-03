"""Public sandbox file-edit verb."""

from __future__ import annotations

from sandbox.api._changeset_projection import committed_paths, conflict_and_status
from sandbox.api.models import EditFileRequest, EditFileResult
from sandbox.occ.changeset.types import EditChange
from sandbox.occ.client import OCCClient
from sandbox.occ.patching.patcher import SearchReplaceEdit as OCCSearchReplaceEdit


async def edit_file(sandbox_id: str, request: EditFileRequest) -> EditFileResult:
    """Apply search/replace edits through the OCC runtime peer."""
    change = EditChange(
        path=request.path,
        edits=tuple(
            OCCSearchReplaceEdit(old_text=edit.old_text, new_text=edit.new_text)
            for edit in request.edits
        ),
    )
    result = await OCCClient(sandbox_id).apply_changeset(
        [change],
        agent_id=request.actor.agent_id,
        description=request.description or f"edit {request.path}",
    )
    paths = committed_paths(result.files, fallback_path=request.path)
    conflict, status = conflict_and_status(result.files)
    return EditFileResult(
        success=result.success,
        changed_paths=paths,
        applied_edits=len(request.edits) if result.success else 0,
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
    )


__all__ = ["edit_file"]
