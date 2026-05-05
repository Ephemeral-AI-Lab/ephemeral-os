"""Public sandbox shell verb."""

from __future__ import annotations

import time
from dataclasses import replace
from uuid import uuid4

from sandbox.api.tool.result_projection import conflict_and_status, published_paths
from sandbox.api.utils.models import ConflictInfo, ShellRequest, ShellResult
from sandbox.occ.client import OCCClient, OCCClientError, get_occ_service
from sandbox.occ.overlay_capture import apply_overlay_capture
from sandbox.occ.service import OccService
from sandbox.overlay.capture.types import read_output_ref
from sandbox.overlay.client import OverlayClientError, get_overlay_client
from sandbox.overlay.runner.snapshot_overlay_runner import OverlayShellRequest
from sandbox.runtime.async_bridge import run_sync_in_executor
from sandbox.runtime.overlay_shell.transaction import (
    OverlayShellCommitResult,
    run_overlay_shell_commit,
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
        overlay_client = get_overlay_client(sandbox_id)
        occ_service = get_occ_service(sandbox_id)
    except (OverlayClientError, OCCClientError) as exc:
        return _error_result(
            reason=getattr(exc, "kind", "overlay_snapshot_required"),
            message=getattr(exc, "message", str(exc)),
            timings={"api.shell.total_s": time.perf_counter() - total_start},
    )

    if isinstance(occ_service, OccService) and overlay_client.runner.supports_sync:
        transaction_start = time.perf_counter()
        transaction = await run_sync_in_executor(
            run_overlay_shell_commit,
            runner=overlay_client.runner,
            occ_service=occ_service,
            request=OverlayShellRequest(
                request_id=uuid4().hex,
                command=("bash", "-lc", request.command),
                cwd=_overlay_cwd(request.cwd),
                env={},
                timeout_seconds=(
                    float(request.timeout) if request.timeout is not None else None
                ),
            ),
            agent_id=request.actor.agent_id,
            description=request.description or "shell",
        )
        outer_elapsed = time.perf_counter() - transaction_start
        timings = dict(transaction.timings)
        worker_elapsed = timings.get("api.shell.worker_total_s", 0.0)
        timings["api.shell.transaction_dispatch_s"] = max(
            0.0,
            outer_elapsed - worker_elapsed,
        )
        timings["api.shell.total_s"] = time.perf_counter() - total_start
        return _success_result(replace(transaction, timings=timings))

    occ_client = OCCClient(service=occ_service)
    overlay_start = time.perf_counter()
    capture = await overlay_client.shell(
        ("bash", "-lc", request.command),
        request_id=uuid4().hex,
        cwd=_overlay_cwd(request.cwd),
        timeout_seconds=float(request.timeout) if request.timeout is not None else None,
    )
    overlay_elapsed = time.perf_counter() - overlay_start
    occ_start = time.perf_counter()
    changeset = await apply_overlay_capture(
        capture,
        occ_client=occ_client,
        agent_id=request.actor.agent_id,
        description=request.description or "shell",
    )
    occ_elapsed = time.perf_counter() - occ_start
    transaction = OverlayShellCommitResult(
        capture=capture,
        changeset=changeset,
        stdout=read_output_ref(capture.stdout_ref),
        stderr=read_output_ref(capture.stderr_ref),
        timings={
            **capture.timings,
            **changeset.timings,
            "api.shell.overlay_s": overlay_elapsed,
            "api.shell.occ_apply_s": occ_elapsed,
            "api.shell.total_s": time.perf_counter() - total_start,
        },
    )
    return _success_result(transaction)


def _success_result(transaction: OverlayShellCommitResult) -> ShellResult:
    capture = transaction.capture
    changeset = transaction.changeset
    conflict, conflict_status = conflict_and_status(changeset.files)
    command_failed = capture.exit_code != 0
    success = not command_failed and changeset.success
    status = "ok" if success else conflict_status if conflict is not None else "error"
    return ShellResult(
        success=success,
        exit_code=capture.exit_code,
        stdout=transaction.stdout,
        stderr=transaction.stderr,
        changed_paths=published_paths(changeset.files),
        status=status,
        conflict=conflict,
        conflict_reason=conflict.message if conflict is not None else None,
        warnings=(),
        timings=transaction.timings,
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
