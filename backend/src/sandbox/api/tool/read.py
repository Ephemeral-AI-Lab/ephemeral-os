"""Public sandbox file-read verb."""

from __future__ import annotations

from sandbox.api.tool._runtime import call_runtime_api, timings_from_payload
from sandbox.api.utils.models import ReadFileRequest, ReadFileResult


async def read_file(sandbox_id: str, request: ReadFileRequest) -> ReadFileResult:
    """Read one UTF-8 text file through the sandbox runtime layer stack."""
    raw = await call_runtime_api(
        sandbox_id,
        "api.read_file",
        {"path": request.path},
        timeout=60,
    )
    return ReadFileResult(
        success=bool(raw.get("success", False)),
        exists=bool(raw.get("exists", False)),
        content=str(raw.get("content", "")),
        encoding=str(raw.get("encoding", "utf-8")),
        timings=timings_from_payload(raw.get("timings")),
    )


__all__ = ["read_file"]
