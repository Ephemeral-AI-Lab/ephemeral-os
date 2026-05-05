"""Single-worker overlay shell plus OCC apply transaction."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.overlay_capture import apply_overlay_capture_sync
from sandbox.occ.service import OccService
from sandbox.overlay.capture.types import OverlayCapture, read_output_ref
from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)


@dataclass(frozen=True)
class OverlayShellCommitResult:
    capture: OverlayCapture
    changeset: ChangesetResult
    stdout: str
    stderr: str
    timings: dict[str, float]


def run_overlay_shell_commit(
    *,
    runner: SnapshotOverlayRunner,
    occ_service: OccService,
    request: OverlayShellRequest,
    agent_id: str = "",
    description: str = "",
) -> OverlayShellCommitResult:
    """Run overlay capture and OCC apply in one executor-side transaction."""
    total_start = time.perf_counter()
    overlay_start = time.perf_counter()
    capture = runner.shell_sync(request)
    overlay_elapsed = time.perf_counter() - overlay_start

    occ_start = time.perf_counter()
    changeset = apply_overlay_capture_sync(
        capture,
        occ_service=occ_service,
        agent_id=agent_id,
        description=description,
    )
    occ_elapsed = time.perf_counter() - occ_start

    worker_elapsed = time.perf_counter() - total_start
    timings = {
        **capture.timings,
        **changeset.timings,
        "api.shell.overlay_s": overlay_elapsed,
        "api.shell.occ_apply_s": occ_elapsed,
        "api.shell.worker_total_s": worker_elapsed,
        "api.shell.total_s": worker_elapsed,
    }
    return OverlayShellCommitResult(
        capture=capture,
        changeset=changeset,
        stdout=read_output_ref(capture.stdout_ref),
        stderr=read_output_ref(capture.stderr_ref),
        timings=timings,
    )


__all__ = ["OverlayShellCommitResult", "run_overlay_shell_commit"]
