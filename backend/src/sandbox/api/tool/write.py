"""Public sandbox file-write verb."""

from __future__ import annotations

from sandbox.api.tool._runtime import (
    call_runtime_api,
    conflict_from_payload,
    paths_from_payload,
    timings_from_payload,
)
from sandbox.api.utils.models import WriteFileRequest, WriteFileResult


async def write_file(sandbox_id: str, request: WriteFileRequest) -> WriteFileResult:
    """Write one UTF-8 file through sandbox-local OCC."""
    raw = await call_runtime_api(
        sandbox_id,
        "api.write_file",
        {
            "path": request.path,
            "content": request.content,
            "actor_id": request.caller.agent_id,
            "description": request.description or f"write {request.path}",
            "overwrite": request.overwrite,
        },
        timeout=60,
    )
    conflict = conflict_from_payload(raw.get("conflict"))
    return WriteFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=paths_from_payload(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=timings_from_payload(raw.get("timings")),
    )


__all__ = ["write_file"]
