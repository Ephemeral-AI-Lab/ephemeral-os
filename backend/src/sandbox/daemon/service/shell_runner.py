"""Runtime-local command-exec server for guarded shell calls."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shutil
from uuid import uuid4

from sandbox.execution import (
    CommandExecRequest,
    CommandExecResult,
    OCCMutationClient,
    WorkspaceLeaseClient,
    execute_command,
    run_workspace_replaced_command,
)
from sandbox.execution.contract import WorkspaceCapture
from sandbox.execution.contract import MountMode
from sandbox.execution.subprocess_runner import (
    child_cpu_times,
    record_child_cpu_delta,
    run_command_to_refs,
)
from sandbox.execution.resource_audit import command_exec_resource_timings
from sandbox.layer_stack.workspace_binding import require_workspace_binding
from sandbox.occ.gitignore import SnapshotGitignoreOracle
from sandbox.daemon.occ_backend import build_occ_backend
from sandbox.daemon.request_context import require_layer_stack_root
from sandbox.daemon.result_projection import (
    conflict_and_status,
    conflict_to_dict,
    gitignore_cache_timings,
    published_paths,
)
from sandbox.daemon.service.sandbox_overlay import SandboxOverlay
from sandbox.daemon.service.overlay_manager import get_sandbox_overlay
from sandbox._shared.clock import monotonic_now


async def execute_shell_api(args: dict[str, object]) -> dict[str, object]:
    """Public ``api.shell`` execution entrypoint used by the handler layer."""
    backend = build_occ_backend(require_layer_stack_root(args))
    result = await _execute_shell(
        args,
        layer_stack=backend.layer_stack,
        occ_client=backend.occ_client,
        gitignore=backend.gitignore,
        storage_root=backend.layer_stack.storage_root,
    )
    return _payload_from_result(result)


async def _execute_shell(
    args: Mapping[str, object],
    *,
    layer_stack: WorkspaceLeaseClient,
    occ_client: OCCMutationClient,
    gitignore: SnapshotGitignoreOracle,
    storage_root: Path,
) -> CommandExecResult:
    request = _command_request(args)
    overlay = await get_sandbox_overlay(
        request.workspace_ref,
        workspace_root=request.workspace_root,
        start=True,
    )
    if overlay.is_mounted:
        return await _execute_persistent_shell(
            request,
            overlay=overlay,
            gitignore=gitignore,
            storage_root=storage_root,
        )
    overlay = SandboxOverlay(
        occ_client=occ_client,
        workspace_ref=request.workspace_ref,
        layer_stack=layer_stack,
        workspace_root=request.workspace_root,
    )
    return await execute_command(
        request,
        layer_stack=layer_stack,
        capture_publisher=overlay,
        storage_root=storage_root,
        timing_provider=lambda: gitignore_cache_timings(gitignore),
        command_runner=run_workspace_replaced_command,
    )


async def _execute_persistent_shell(
    request: CommandExecRequest,
    *,
    overlay: SandboxOverlay,
    gitignore: SnapshotGitignoreOracle,
    storage_root: Path,
) -> CommandExecResult:
    total_start = monotonic_now()
    run_dir = _persistent_run_dir(storage_root, request.request_id)
    stdout_ref = run_dir / "stdout.bin"
    stderr_ref = run_dir / "stderr.bin"
    timings: dict[str, float] = {
        "command_exec.mount_workspace_s": 0.0,
        "command_exec.handler_sync_prelude_s": 0.0,
    }
    try:
        async with overlay.workspace_operation(reason=f"cmd:{request.request_id}:enter") as snapshot:
            run_start = monotonic_now()
            cpu_start = child_cpu_times()
            exit_code = run_command_to_refs(
                command=request.command,
                declared_workspace_root=request.workspace_root,
                mounted_workspace_root=request.workspace_root,
                cwd=request.cwd,
                env=request.env,
                timeout_seconds=request.timeout_seconds,
                stdout_ref=stdout_ref,
                stderr_ref=stderr_ref,
            )
            timings["command_exec.run_command_s"] = monotonic_now() - run_start
            record_child_cpu_delta(timings, cpu_start)
            publish = await overlay.publish_pending_changes(
                snapshot=snapshot,
                reason="publish",
                run_maintenance=False,
            )
            timings.update(publish.timings)
            if "overlay.capture_upperdir_s" in publish.timings:
                timings["command_exec.capture_upperdir_s"] = publish.timings[
                    "overlay.capture_upperdir_s"
                ]
            if "overlay.occ_apply_s" in publish.timings:
                timings["command_exec.occ_apply_s"] = publish.timings[
                    "overlay.occ_apply_s"
                ]
            maintenance_timings = await overlay.run_maintenance_after_publish(
                publish.changeset,
                workspace_ref=request.workspace_ref,
            )
        changeset = publish.changeset
        timings = {
            **timings,
            **changeset.timings,
            **maintenance_timings,
            **gitignore_cache_timings(gitignore),
            **command_exec_resource_timings(
                storage_root=storage_root,
                scratch_root=storage_root,
                run_dir=run_dir,
                upperdir=overlay.upperdir,
                manifest=snapshot,
                changed_path_count=len(publish.path_changes),
            ),
        }
        timings["api.shell.overlay_s"] = (
            timings.get("command_exec.mount_workspace_s", 0.0)
            + timings.get("command_exec.run_command_s", 0.0)
            + timings.get("command_exec.capture_upperdir_s", 0.0)
        )
        timings["api.shell.occ_apply_s"] = timings.get("command_exec.occ_apply_s", 0.0)
        timings["command_exec.total_s"] = monotonic_now() - total_start
        timings["api.shell.total_s"] = timings["command_exec.total_s"]
        return CommandExecResult(
            exit_code=exit_code,
            stdout=stdout_ref.read_bytes().decode("utf-8", "replace"),
            stderr=stderr_ref.read_bytes().decode("utf-8", "replace"),
            stdout_ref=stdout_ref.as_posix(),
            stderr_ref=stderr_ref.as_posix(),
            workspace_capture=WorkspaceCapture(
                changes=publish.path_changes,
                snapshot_version=int(snapshot.version),
                mount_mode=MountMode.PRIVATE_NAMESPACE,
                snapshot_manifest=snapshot,
            ),
            occ_result=changeset,
            timings=timings,
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _persistent_run_dir(storage_root: Path, request_id: str) -> Path:
    safe_id = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in request_id
    ).strip("-")
    run_dir = storage_root / "runtime" / "command_exec_persistent" / (
        f"{safe_id or 'request'}-{uuid4().hex[:8]}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _payload_from_result(result: CommandExecResult) -> dict[str, object]:
    changeset = result.occ_result
    files = getattr(changeset, "files", ())
    conflict, conflict_status = conflict_and_status(files)
    command_failed = result.exit_code != 0
    success = not command_failed and bool(getattr(changeset, "success", False))
    status = "ok" if success else conflict_status if conflict is not None else "error"
    return {
        "success": success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "changed_paths": list(published_paths(files)),
        "status": status,
        "conflict": conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "workspace_capture": {
            "snapshot_version": result.workspace_capture.snapshot_version,
            "mount_mode": result.workspace_capture.mount_mode,
            "changes": [
                change.to_dict() if hasattr(change, "to_dict") else str(change)
                for change in result.workspace_capture.changes
            ],
        },
        "warnings": [],
        "timings": result.timings,
    }


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
    workspace_ref = require_layer_stack_root(args)
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


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")
