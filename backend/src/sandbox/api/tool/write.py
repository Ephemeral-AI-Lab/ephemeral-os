"""Public sandbox file-write verb."""

from __future__ import annotations

import time

from sandbox.api.tool.result_projection import committed_paths, conflict_and_status
from sandbox.api.utils.models import WriteFileRequest, WriteFileResult
from sandbox.occ.changeset.builders import build_api_write_change
from sandbox.occ.client import OCCClient


async def write_file(sandbox_id: str, request: WriteFileRequest) -> WriteFileResult:
    """Write one UTF-8 file through the typed OCC service path.

    The service infers base hashes from the leased snapshot when one is bound
    to the request path. ``create_only=True`` (when ``overwrite=False``) still
    prevents accidental creation-overwrite.
    """
    total_start = time.perf_counter()
    build_start = time.perf_counter()
    change = build_api_write_change(
        path=request.path,
        final_content=request.content,
        create_only=not request.overwrite,
    )
    build_elapsed = time.perf_counter() - build_start
    occ_start = time.perf_counter()
    result = await OCCClient(sandbox_id).apply_changeset(
        [change],
        agent_id=request.actor.agent_id,
        description=request.description or f"write {request.path}",
    )
    occ_elapsed = time.perf_counter() - occ_start
    paths = committed_paths(result.files, fallback_path=request.path)
    conflict, status = conflict_and_status(result.files)
    timings = {
        **result.timings,
        "api.write.build_change_s": build_elapsed,
        "api.write.occ_apply_s": occ_elapsed,
        "api.write.total_s": time.perf_counter() - total_start,
    }
    return WriteFileResult(
        success=result.success,
        changed_paths=paths,
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
        timings=timings,
    )


__all__ = ["write_file"]
