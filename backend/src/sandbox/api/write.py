"""Public sandbox file-write verb."""

from __future__ import annotations

from sandbox.api._changeset_projection import committed_paths, conflict_and_status
from sandbox.api.models import WriteFileRequest, WriteFileResult
from sandbox.occ.changeset.builders import build_api_write_change
from sandbox.occ.client import OCCClient


async def write_file(sandbox_id: str, request: WriteFileRequest) -> WriteFileResult:
    """Write one UTF-8 file through the OCC runtime peer.

    The host does not pin a base hash; the gate's per-file lock guards the
    write atomically. ``create_only=True`` (when ``overwrite=False``) is
    the only case where the gate aborts on existence — see
    ``.omc/plans/occ-changeset-gate-simplification.md`` §"How base_hash is
    obtained".
    """
    change = build_api_write_change(
        path=request.path,
        final_content=request.content,
        create_only=not request.overwrite,
    )
    result = await OCCClient(sandbox_id).apply_changeset(
        [change],
        agent_id=request.actor.agent_id,
        description=request.description or f"write {request.path}",
    )
    paths = committed_paths(result.files, fallback_path=request.path)
    conflict, status = conflict_and_status(result.files)
    return WriteFileResult(
        success=result.success,
        changed_paths=paths,
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
    )


__all__ = ["write_file"]
