"""Command-exec top-level lifecycle: lease -> run -> delegate capture publish.

The ``*_ref`` arguments (stdout_ref, stderr_ref, control_ref) are local
filesystem paths used as IPC handles between the strategy that wrote the
output and the service that reads it back. They are not URLs.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from sandbox.ephemeral_workspace.shell_contract import (
    CommandExecRequest,
    CommandExecResult,
    EmptyChangesetResult,
    LayerPathsLayout,
    ShellProcessResult,
    WorkspaceCapture,
    WorkspaceCapturePublisher,
    WorkspaceLeaseClient,
)
from sandbox.overlay.capture import walk_upperdir
from sandbox.overlay.namespace import run_in_namespace
from sandbox._shared.resource_audit import command_exec_resource_timings
from sandbox.overlay.scratch import command_exec_scratch_root
from sandbox.daemon.async_bridge import run_sync_in_executor
from sandbox.layer_stack.paths import TRANSIENT_LOWERDIR_DIR
from sandbox._shared.clock import monotonic_now

logger = logging.getLogger(__name__)

WorkspaceCommandRunner = Callable[
    ...,
    ShellProcessResult,
]


async def execute_command(
    request: CommandExecRequest,
    *,
    layer_stack: WorkspaceLeaseClient,
    capture_publisher: WorkspaceCapturePublisher | None,
    storage_root: Path,
    timing_provider: Callable[[], Mapping[str, float]] | None = None,
    command_runner: WorkspaceCommandRunner = run_in_namespace,
    occ_apply: bool = True,
) -> CommandExecResult:
    """Run one guarded command through snapshot, capture, and OCC apply."""
    if occ_apply and capture_publisher is None:
        raise ValueError("capture_publisher is required when occ_apply=True")
    total_start = monotonic_now()
    scratch_root = _command_exec_scratch_root(storage_root)
    run_dir = _run_dir(scratch_root, request.request_id)
    keep_capture_artifacts = False
    timings: dict[str, float] = {}
    timings["command_exec.handler_sync_prelude_s"] = (
        monotonic_now() - total_start
    )

    lease_start = monotonic_now()
    lease = layer_stack.prepare_workspace_snapshot(
        request_id=request.request_id,
    )
    timings.update(
        {
            **lease.timings,
            "command_exec.prepare_snapshot_s": monotonic_now() - lease_start,
        }
    )

    released = False
    try:
        if lease.layer_paths is None:
            raise RuntimeError("workspace snapshot did not include layer paths")
        spec = LayerPathsLayout(
            workspace_root=request.workspace_root,
            layer_paths=lease.layer_paths,
            layer_storage_root=str(layer_stack.storage_root),
            writes=str(run_dir / "upper"),
            kernel_scratch=str(run_dir / "work"),
            scratch_root=str(scratch_root),
        )
        runner_kwargs = {
            "spec": spec,
            "request": request,
            "run_dir": run_dir,
            "timings": timings,
        }
        process = await run_sync_in_executor(command_runner, **runner_kwargs)

        if occ_apply:
            assert capture_publisher is not None
            publish = await capture_publisher.publish_cycle(
                request=request,
                upperdir=spec.writes,
                snapshot=lease.manifest,
                run_maintenance=False,
            )
            path_changes = publish.path_changes
            changeset = publish.changeset
            timings.update(publish.timings)
        else:
            capture_start = monotonic_now()
            path_changes = walk_upperdir(spec.writes, timings=timings)
            timings["command_exec.capture_upperdir_s"] = (
                monotonic_now() - capture_start
            )
            changeset = EmptyChangesetResult()
            timings["command_exec.occ_apply_s"] = 0.0
        release_start = monotonic_now()
        layer_stack.release_lease(
            lease_id=lease.lease_id,
        )
        released = True
        _drop_transient_lowerdir(
            lease,
            storage_root=storage_root,
            scratch_root=scratch_root,
        )
        timings["command_exec.release_snapshot_s"] = (
            monotonic_now() - release_start
        )
        maintenance_timings = {}
        if occ_apply:
            assert capture_publisher is not None
            maintenance_timings = await capture_publisher.run_maintenance_after_publish(
                changeset,
                workspace_ref=request.workspace_ref,
            )
        timings = {
            **timings,
            **changeset.timings,
            **maintenance_timings,
            **(timing_provider() if timing_provider is not None else {}),
        }
        timings.update(
            command_exec_resource_timings(
                storage_root=storage_root,
                scratch_root=scratch_root,
                run_dir=run_dir,
                upperdir=Path(spec.writes),
                manifest=lease.manifest,
                changed_path_count=len(path_changes),
            )
        )
        timings["api.shell.overlay_s"] = (
            timings.get("command_exec.mount_workspace_s", 0.0)
            + timings.get("command_exec.run_command_s", 0.0)
            + timings.get("command_exec.capture_upperdir_s", 0.0)
        )
        timings["api.shell.occ_apply_s"] = timings["command_exec.occ_apply_s"]
        timings["command_exec.total_s"] = monotonic_now() - total_start
        timings["api.shell.total_s"] = timings["command_exec.total_s"]
        result = CommandExecResult(
            exit_code=process.exit_code,
            stdout=_read_output_ref(process.stdout_ref),
            stderr=_read_output_ref(process.stderr_ref),
            stdout_ref=process.stdout_ref,
            stderr_ref=process.stderr_ref,
            workspace_capture=WorkspaceCapture(
                changes=path_changes,
                snapshot_version=lease.manifest_version,
                mount_mode=process.mount_mode,
                snapshot_manifest=lease.manifest,
            ),
            occ_result=changeset,
            timings=timings,
        )
        keep_capture_artifacts = not occ_apply
        return result
    finally:
        if not released:
            release_start = monotonic_now()
            layer_stack.release_lease(
                lease_id=lease.lease_id,
            )
            _drop_transient_lowerdir(
                lease,
                storage_root=storage_root,
                scratch_root=scratch_root,
            )
            timings["command_exec.release_snapshot_s"] = (
                monotonic_now() - release_start
            )
        # Capture and OCC commit are done by the time we get here. For normal
        # shell execution, the run_dir tree is no longer load-bearing. The
        # no-OCC snapshot-overlay path returns stdout/stderr/content refs into
        # run_dir, so only its bulk intermediate trees are removed.
        if keep_capture_artifacts:
            _drop_non_capture_run_dir_entries(run_dir)
        else:
            shutil.rmtree(run_dir, ignore_errors=True)


def _drop_non_capture_run_dir_entries(run_dir: Path) -> None:
    # The namespace path mounts in-place. Keep the historical cleanup helper
    # for no-OCC capture callers that may still preserve stdout/stderr refs.
    for name in ("workspace", "work"):
        shutil.rmtree(run_dir / name, ignore_errors=True)


def _read_output_ref(path: str) -> str:
    return Path(path).read_bytes().decode("utf-8", "replace")


def _run_dir(storage_root: Path, request_id: str) -> Path:
    safe_id = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in request_id
    ).strip("-")
    return storage_root / "runtime" / "command_exec" / (
        f"{safe_id or 'request'}-{uuid4().hex[:8]}"
    )


def _command_exec_scratch_root(storage_root: Path) -> Path:
    return command_exec_scratch_root(storage_root)


def _drop_transient_lowerdir(
    lease: object,
    *,
    storage_root: Path,
    scratch_root: Path | None = None,
) -> None:
    lowerdir_val = getattr(lease, "lowerdir", None)
    if lowerdir_val is None:
        return
    raw = str(lowerdir_val).strip()
    if not raw:
        return
    lowerdir = Path(raw)
    scratch_dir = lowerdir.parent
    effective_scratch_root = scratch_root or storage_root
    transient_roots = {
        (storage_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(strict=False),
        (effective_scratch_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(
            strict=False
        ),
    }
    scratch_parent = scratch_dir.parent.resolve(strict=False)
    if (
        lowerdir.name != "lower"
        or scratch_dir.parent.name != TRANSIENT_LOWERDIR_DIR
        or scratch_parent not in transient_roots
    ):
        logger.warning(
            "refusing to drop unexpected transient lowerdir path: %s",
            lowerdir,
        )
        return
    try:
        shutil.rmtree(scratch_dir)
    except OSError:
        logger.warning(
            "failed to drop transient lowerdir scratch dir: %s",
            scratch_dir,
            exc_info=True,
        )


__all__ = [
    "execute_command",
]
