"""Single-worker overlay shell plus OCC apply transaction."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sandbox.occ.changeset.intent import CommitIntent, PreparedChangeset
from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.service import OccService
from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)
from sandbox.runtime.overlay_shell.capture_to_changeset import capture_to_changeset
from sandbox.runtime.overlay_shell.pipeline import read_output_ref
from sandbox.runtime.overlay_shell.result_envelope import RuntimeResultEnvelope


@dataclass(frozen=True)
class ShellTransactionResult:
    envelope: RuntimeResultEnvelope
    changeset: ChangesetResult
    stdout: str
    stderr: str
    timings: dict[str, float]


def run_shell_transaction(
    *,
    runner: SnapshotOverlayRunner,
    occ_service: OccService,
    request: OverlayShellRequest,
    agent_id: str = "",
    description: str = "",
) -> ShellTransactionResult:
    """Run overlay capture and OCC apply in one executor-side transaction."""
    total_start = time.perf_counter()
    overlay_start = time.perf_counter()
    envelope = runner.shell_sync(request)
    overlay_elapsed = time.perf_counter() - overlay_start

    occ_start = time.perf_counter()
    changeset = _apply_envelope_changes_sync(
        envelope,
        occ_service=occ_service,
        agent_id=agent_id,
        description=description,
    )
    occ_elapsed = time.perf_counter() - occ_start

    worker_elapsed = time.perf_counter() - total_start
    timings = {
        **envelope.timings,
        **changeset.timings,
        "api.shell.overlay_s": overlay_elapsed,
        "api.shell.occ_apply_s": occ_elapsed,
        "api.shell.worker_total_s": worker_elapsed,
        "api.shell.total_s": worker_elapsed,
    }
    return ShellTransactionResult(
        envelope=envelope,
        changeset=changeset,
        stdout=read_output_ref(envelope.stdout_ref),
        stderr=read_output_ref(envelope.stderr_ref),
        timings=timings,
    )


def _apply_envelope_changes_sync(
    envelope: RuntimeResultEnvelope,
    *,
    occ_service: OccService,
    agent_id: str,
    description: str,
) -> ChangesetResult:
    changes = capture_to_changeset(envelope.upper_changes)
    if not changes:
        return ChangesetResult(
            files=(),
            timings=dict(envelope.timings),
            published_manifest_version=None,
        )
    if envelope.snapshot_manifest is None:
        raise ValueError("overlay shell envelope is missing its leased manifest")
    result = occ_service.apply_changeset_sync(
        changes,
        snapshot=envelope.snapshot_manifest,
        options=CommitIntent(caller_id=agent_id, description=description),
    )
    if isinstance(result, PreparedChangeset):
        raise TypeError("shell capture OCC service returned an uncommitted changeset")
    return result


__all__ = ["ShellTransactionResult", "run_shell_transaction"]
