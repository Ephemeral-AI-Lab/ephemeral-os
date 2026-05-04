"""Public sandbox shell verb."""

from __future__ import annotations

import time
from uuid import uuid4

from sandbox.api.utils.changeset_projection import conflict_and_status, published_paths
from sandbox.api.utils.models import ConflictInfo, ShellRequest, ShellResult
from sandbox.occ.client import OCCClient, OCCClientError
from sandbox.overlay.client import OverlayClient, OverlayClientError
from sandbox.runtime.overlay_shell.pipeline import (
    apply_captured_changes,
    read_output_ref,
)


async def shell(sandbox_id: str, request: ShellRequest) -> ShellResult:
    """Run a shell command through the snapshot overlay and typed OCC path."""
    total_start = time.perf_counter()
    if request.stdin is not None:
        return _error_result(
            reason="stdin_not_supported",
            message="snapshot overlay shell does not accept stdin",
            timings={"api.shell.total_s": time.perf_counter() - total_start},
        )

    try:
        overlay_client = OverlayClient(sandbox_id)
        occ_client = OCCClient(sandbox_id)
    except (OverlayClientError, OCCClientError) as exc:
        return _error_result(
            reason=getattr(exc, "kind", "overlay_snapshot_required"),
            message=getattr(exc, "message", str(exc)),
            timings={"api.shell.total_s": time.perf_counter() - total_start},
        )

    overlay_start = time.perf_counter()
    envelope = await overlay_client.shell(
        ("bash", "-lc", request.command),
        request_id=uuid4().hex,
        cwd=_overlay_cwd(request.cwd),
        timeout_seconds=float(request.timeout) if request.timeout is not None else None,
    )
    overlay_elapsed = time.perf_counter() - overlay_start
    occ_start = time.perf_counter()
    changeset = await apply_captured_changes(
        envelope,
        occ_client=occ_client,
        agent_id=request.actor.agent_id,
        description=request.description or "shell",
    )
    occ_elapsed = time.perf_counter() - occ_start
    conflict, conflict_status = conflict_and_status(changeset.files)
    command_failed = envelope.exit_code != 0
    success = not command_failed and changeset.success
    status = "ok" if success else conflict_status if conflict is not None else "error"
    timings = {
        **envelope.timings,
        **changeset.timings,
        "api.shell.overlay_s": overlay_elapsed,
        "api.shell.occ_apply_s": occ_elapsed,
        "api.shell.total_s": time.perf_counter() - total_start,
    }
    return ShellResult(
        success=success,
        exit_code=envelope.exit_code,
        stdout=read_output_ref(envelope.stdout_ref),
        stderr=read_output_ref(envelope.stderr_ref),
        changed_paths=published_paths(changeset.files),
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
        warnings=(),
        timings=timings,
    )


def _error_result(
    *,
    reason: str,
    message: str,
    timings: dict[str, float] | None = None,
) -> ShellResult:
    conflict = ConflictInfo(reason=reason, message=message)
    return ShellResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="",
        changed_paths=(),
        status="error",
        conflict=conflict,
        conflict_reason=message,
        warnings=(),
        timings=timings or {},
    )


def _overlay_cwd(cwd: str | None) -> str:
    if cwd is None or not str(cwd).strip():
        return "."
    if str(cwd).startswith("/"):
        return "."
    return str(cwd)


__all__ = ["shell"]
