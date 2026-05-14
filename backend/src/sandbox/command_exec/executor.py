"""Command-exec orchestration service."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from sandbox.command_exec.contract import (
    CommandExecRequest,
    CommandExecResult,
    MountMode,
    OCCMutationClient,
    ShellProcessResult,
    WorkspaceCapture,
    WorkspaceLeaseClient,
    WorkspaceReplacementMountSpec,
)
from sandbox.command_exec.workspace.capture import capture_workspace_upperdir
from sandbox.command_exec.workspace.mount import run_workspace_replaced_command
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.workspace.binding import require_workspace_binding
from sandbox.occ import ChangesetResult, CommitOptions
from sandbox.occ.capture.overlay import overlay_path_changes_to_occ_changes
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.result_projection import gitignore_cache_timings
from sandbox.overlay import OverlayPathChange, read_output_ref
from sandbox.async_bridge import run_sync_in_executor
from sandbox.timing import monotonic_now

logger = logging.getLogger(__name__)

_TRANSIENT_LOWERDIR_DIR = "transient-lowerdirs"

WorkspaceCommandRunner = Callable[
    ...,
    ShellProcessResult,
]


async def execute_command(
    args: Mapping[str, object],
    *,
    layer_stack: WorkspaceLeaseClient,
    occ_client: OCCMutationClient,
    gitignore: SnapshotGitignoreOracle,
    storage_root: Path,
    command_runner: WorkspaceCommandRunner = run_workspace_replaced_command,
) -> CommandExecResult:
    """Run one guarded command through snapshot, capture, and OCC apply."""
    total_start = monotonic_now()
    request = _command_request(args)
    run_dir = _run_dir(storage_root, request.request_id)
    timings: dict[str, float] = {}
    timings["command_exec.handler_sync_prelude_s"] = (
        monotonic_now() - total_start
    )

    lease_start = monotonic_now()
    lease = layer_stack.prepare_workspace_snapshot(
        workspace_ref=request.workspace_ref,
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
        spec = WorkspaceReplacementMountSpec(
            workspace_root=request.workspace_root,
            lowerdir=lease.lowerdir,
            upperdir=str(run_dir / "upper"),
            workdir=str(run_dir / "work"),
            scratch_root=str(storage_root),
        )
        process = await run_sync_in_executor(
            command_runner,
            spec=spec,
            request=request,
            run_dir=run_dir,
            timings=timings,
        )

        capture_start = monotonic_now()
        path_changes = tuple(
            capture_workspace_upperdir(
                spec=spec,
                mounted_workspace_root=process.mounted_workspace_root,
                copy_backed=process.mount_mode == MountMode.COPY_BACKED,
                timings=timings,
            )
        )
        timings["command_exec.capture_upperdir_s"] = (
            monotonic_now() - capture_start
        )

        occ_start = monotonic_now()
        changeset = await _apply_workspace_capture(
            path_changes,
            occ_client=occ_client,
            snapshot=lease.manifest,
            request=request,
        )
        timings["command_exec.occ_apply_s"] = monotonic_now() - occ_start
        release_start = monotonic_now()
        layer_stack.release_lease(
            workspace_ref=request.workspace_ref,
            lease_id=lease.lease_id,
        )
        released = True
        _drop_transient_lowerdir(lease, storage_root=storage_root)
        timings["command_exec.release_snapshot_s"] = (
            monotonic_now() - release_start
        )
        timings = {
            **timings,
            **changeset.timings,
            **gitignore_cache_timings(gitignore),
        }
        timings["api.shell.overlay_s"] = (
            timings.get("command_exec.mount_workspace_s", 0.0)
            + timings.get("command_exec.run_command_s", 0.0)
            + timings.get("command_exec.capture_upperdir_s", 0.0)
        )
        timings["api.shell.occ_apply_s"] = timings["command_exec.occ_apply_s"]
        timings["command_exec.total_s"] = monotonic_now() - total_start
        timings["api.shell.total_s"] = timings["command_exec.total_s"]
        return CommandExecResult(
            exit_code=process.exit_code,
            stdout=read_output_ref(process.stdout_ref),
            stderr=read_output_ref(process.stderr_ref),
            workspace_capture=WorkspaceCapture(
                changes=path_changes,
                snapshot_version=lease.manifest_version,
                mount_mode=process.mount_mode,
            ),
            occ_result=changeset,
            timings=timings,
        )
    finally:
        if not released:
            release_start = monotonic_now()
            layer_stack.release_lease(
                workspace_ref=request.workspace_ref,
                lease_id=lease.lease_id,
            )
            _drop_transient_lowerdir(lease, storage_root=storage_root)
            timings["command_exec.release_snapshot_s"] = (
                monotonic_now() - release_start
            )
        # Capture and OCC commit are done by the time we get here; the
        # run_dir tree is no longer load-bearing. ignore_errors keeps
        # cleanup non-fatal so a stale dir cannot mask a real exception.
        shutil.rmtree(run_dir, ignore_errors=True)


async def _apply_workspace_capture(
    path_changes: Sequence[OverlayPathChange],
    *,
    occ_client: OCCMutationClient,
    snapshot: Manifest,
    request: CommandExecRequest,
) -> ChangesetResult:
    typed_changes = overlay_path_changes_to_occ_changes(path_changes)
    if not typed_changes:
        return ChangesetResult(
            files=(),
            timings={},
            published_manifest_version=None,
        )
    # Single-path captures opt out of cross-path atomicity so
    # ``OccSerialMerger._disjoint_batches`` can coalesce them with other
    # concurrent disjoint commits. Multi-path captures keep ``atomic=True``
    # so a single failed validation rejects the whole capture.
    distinct_paths = {change.path for change in typed_changes}
    is_atomic = len(distinct_paths) > 1
    result = await occ_client.apply_changeset(
        typed_changes,
        snapshot=snapshot,
        options=CommitOptions(atomic=is_atomic),
        workspace_ref=request.workspace_ref,
    )
    return result


# WR-08: conservative argv-size cap below typical Linux ARG_MAX (~128 KiB).
# A caller pushing a large blob into a single argv element used to trip
# the kernel's E2BIG at exec time with an opaque OSError; this surfaces a
# structured ValueError before the syscall.
_MAX_ARGV_BYTES = 128 * 1024


def _command_request(args: Mapping[str, object]) -> CommandExecRequest:
    command = args.get("command")
    if isinstance(command, str):
        argv: tuple[str, ...] = ("bash", "-lc", command)
    elif isinstance(command, list):
        argv = tuple(str(part) for part in command)
    else:
        raise ValueError("command must be a string or argv list")
    argv_bytes = sum(len(part.encode("utf-8")) for part in argv) + len(argv)
    if argv_bytes > _MAX_ARGV_BYTES:
        raise ValueError(
            f"argv exceeds {_MAX_ARGV_BYTES} bytes ({argv_bytes}); "
            "stream large blobs via stdin instead"
        )
    timeout = args.get("timeout_seconds", args.get("timeout"))
    workspace_ref = layer_stack_root(args)
    binding = require_workspace_binding(workspace_ref)
    env = _safe_env(_mapping(args.get("env")))
    return CommandExecRequest(
        request_id=str(args.get("request_id") or uuid4().hex),
        workspace_ref=workspace_ref,
        workspace_root=binding.workspace_root,
        command=argv,
        cwd=str(args.get("cwd") or "."),
        env=env,
        timeout_seconds=_optional_float(timeout),
        actor_id=str(args.get("actor_id") or ""),
        description=str(args.get("description") or "shell"),
    )


def _safe_env(raw: Mapping[object, object]) -> dict[str, str]:
    """Validate caller env mapping; reject NUL / ``=`` / empty keys (WR-04)."""
    result: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k)
        value = str(v)
        if not key:
            raise ValueError("env entry has empty key")
        if "\0" in key or "\0" in value:
            raise ValueError(f"env entry contains NUL byte: {key!r}")
        if "=" in key:
            # execvpe constructs `NAME=VALUE`; a `=` in NAME silently
            # corrupts the child env.
            raise ValueError(f"env key cannot contain '=': {key!r}")
        result[key] = value
    return result


def layer_stack_root(args: Mapping[str, object]) -> str:
    """Return the required layer-stack root from API args."""
    layer_stack_root_value = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root_value:
        raise ValueError("layer_stack_root is required")
    return layer_stack_root_value


def _run_dir(storage_root: Path, request_id: str) -> Path:
    safe_id = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in request_id
    ).strip("-")
    run_parent = _command_exec_runtime_root(storage_root)
    return run_parent / f"{safe_id or 'request'}-{uuid4().hex[:8]}"


def _command_exec_runtime_root(storage_root: Path) -> Path:
    return storage_root / "runtime" / "command_exec"


def _drop_transient_lowerdir(lease: object, *, storage_root: Path) -> None:
    raw = str(getattr(lease, "lowerdir", "")).strip()
    if not raw:
        return
    lowerdir = Path(raw)
    scratch_dir = lowerdir.parent
    transient_root = storage_root / "runtime" / _TRANSIENT_LOWERDIR_DIR
    if (
        lowerdir.name != "lower"
        or scratch_dir.parent.name != _TRANSIENT_LOWERDIR_DIR
        or not scratch_dir.resolve(strict=False).is_relative_to(
            transient_root.resolve(strict=False)
        )
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


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


__all__ = [
    "execute_command",
    "layer_stack_root",
]
