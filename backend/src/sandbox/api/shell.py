"""Public sandbox shell verb."""

from __future__ import annotations

from sandbox.api.utils.models import ConflictInfo, ShellRequest, ShellResult
from sandbox.api.raw_exec import raw_exec
from sandbox.api.utils.shell_routing import is_read_only_pipeline


async def shell(sandbox_id: str, request: ShellRequest) -> ShellResult:
    """Run read-only shell directly and reject mutating shell without a snapshot."""
    if is_read_only_pipeline(request.command) and request.stdin is None:
        return await _raw_shell(sandbox_id, request)

    conflict = ConflictInfo(
        reason="overlay_snapshot_required",
        message=(
            "legacy live-root shell runtime was removed; "
            "shell mutation requests must use the layer-stack snapshot path"
        ),
    )
    return ShellResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="",
        changed_paths=(),
        status="error",
        conflict=conflict,
        conflict_reason=conflict.message,
        warnings=("legacy live-root shell runtime was removed",),
    )


async def _raw_shell(sandbox_id: str, request: ShellRequest) -> ShellResult:
    result = await raw_exec(
        sandbox_id,
        request.command,
        cwd=request.cwd,
        timeout=request.timeout,
    )
    return ShellResult(
        success=True,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        changed_paths=(),
        status="ok",
        conflict=None,
        conflict_reason=None,
        warnings=(),
    )


__all__ = ["shell"]
