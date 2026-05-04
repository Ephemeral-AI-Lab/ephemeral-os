"""Public sandbox file-edit verb."""

from __future__ import annotations

import time

from sandbox.api.utils.changeset_projection import committed_paths, conflict_and_status
from sandbox.api.utils.models import EditFileRequest, EditFileResult
from sandbox.occ.changeset.builders import build_api_edit_change
from sandbox.occ.client import OCCClient


async def edit_file(sandbox_id: str, request: EditFileRequest) -> EditFileResult:
    """Apply search/replace edits through the typed OCC service path."""
    total_start = time.perf_counter()
    build_start = time.perf_counter()
    changes = [
        build_api_edit_change(
            path=request.path,
            old_text=edit.old_text,
            new_text=edit.new_text,
        )
        for edit in request.edits
    ]
    build_elapsed = time.perf_counter() - build_start
    occ_start = time.perf_counter()
    result = await OCCClient(sandbox_id).apply_changeset(
        changes,
        agent_id=request.actor.agent_id,
        description=request.description or f"edit {request.path}",
    )
    occ_elapsed = time.perf_counter() - occ_start
    paths = committed_paths(result.files, fallback_path=request.path)
    conflict, status = conflict_and_status(result.files)
    timings = {
        **result.timings,
        "api.edit.build_changes_s": build_elapsed,
        "api.edit.occ_apply_s": occ_elapsed,
        "api.edit.total_s": time.perf_counter() - total_start,
    }
    return EditFileResult(
        success=result.success,
        changed_paths=paths,
        applied_edits=len(request.edits) if result.success else 0,
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
        timings=timings,
    )


__all__ = ["edit_file"]
