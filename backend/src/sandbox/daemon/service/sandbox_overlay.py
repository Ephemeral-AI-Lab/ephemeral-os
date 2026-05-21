"""Daemon-owned overlay publish boundary for command and plugin writers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sandbox.execution.contract import (
    ChangesetResultLike,
    CommandExecRequest,
    OCCMutationClient,
    SnapshotManifest,
    WorkspaceCapturePublishResult,
)
from sandbox.execution.overlay.capture import walk_upperdir
from sandbox.execution.path_change import OverlayPathChange
from sandbox.occ.changeset import ChangesetResult, CommitOptions
from sandbox.occ.overlay_change_conversion import overlay_path_changes_to_occ_changes
from sandbox._shared.clock import monotonic_now


class SandboxOverlay:
    """Facade hiding capture and OCC publication behind the daemon boundary.

    The current implementation still receives a per-command upperdir from the
    command runner. The ownership boundary is the important part: callers ask
    the daemon overlay service to publish, and this class owns conversion,
    OCC options, and post-publish maintenance.
    """

    def __init__(
        self,
        *,
        occ_client: OCCMutationClient,
        workspace_ref: str,
    ) -> None:
        self._occ_client = occ_client
        self._workspace_ref = workspace_ref

    async def publish_cycle(
        self,
        *,
        request: CommandExecRequest,
        upperdir: str | Path,
        snapshot: SnapshotManifest,
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult:
        timings: dict[str, float] = {}
        capture_start = monotonic_now()
        path_changes = walk_upperdir(upperdir, timings=timings)
        timings["command_exec.capture_upperdir_s"] = (
            monotonic_now() - capture_start
        )

        occ_start = monotonic_now()
        changeset = await self._apply_workspace_capture(
            path_changes,
            snapshot=snapshot,
            request=request,
            run_maintenance=run_maintenance,
        )
        timings["command_exec.occ_apply_s"] = monotonic_now() - occ_start
        return WorkspaceCapturePublishResult(
            path_changes=path_changes,
            changeset=changeset,
            timings=timings,
        )

    async def run_maintenance_after_publish(
        self,
        result: ChangesetResultLike,
        *,
        workspace_ref: str | None = None,
    ) -> dict[str, float]:
        return await self._occ_client.run_maintenance_after_publish(
            result,
            workspace_ref=workspace_ref or self._workspace_ref,
        )

    async def _apply_workspace_capture(
        self,
        path_changes: Sequence[OverlayPathChange],
        *,
        snapshot: SnapshotManifest,
        request: CommandExecRequest,
        run_maintenance: bool = True,
    ) -> ChangesetResult:
        typed_changes = overlay_path_changes_to_occ_changes(path_changes)
        if not typed_changes:
            return ChangesetResult(
                files=(),
                timings={},
                published_manifest_version=None,
            )
        # Single-path captures opt out of cross-path atomicity so
        # CommitQueue._disjoint_batches can coalesce them with other
        # concurrent disjoint commits. Multi-path captures keep atomic=True
        # so a single failed validation rejects the whole capture.
        distinct_paths = {change.path for change in typed_changes}
        is_atomic = len(distinct_paths) > 1
        return await self._occ_client.apply_changeset(
            typed_changes,
            snapshot=snapshot,
            options=CommitOptions(atomic=is_atomic),
            workspace_ref=request.workspace_ref,
            run_maintenance=run_maintenance,
        )


__all__ = ["SandboxOverlay"]
