"""Runtime-local command-exec server for guarded shell calls."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from uuid import uuid4

from sandbox.api.tool.result_projection import (
    conflict_and_status,
    published_paths,
)
from sandbox.command_exec.capture.changeset import workspace_changes_to_occ_changes
from sandbox.command_exec.capture.upperdir import capture_workspace_upperdir
from sandbox.command_exec.clients import OCCMutationClient, WorkspaceLeaseClient
from sandbox.command_exec.request import CommandExecRequest
from sandbox.command_exec.result import CommandExecResult, WorkspaceCapture
from sandbox.command_exec.workspace_mount import (
    WorkspaceReplacementMountSpec,
    run_workspace_replaced_command,
)
from sandbox.layer_stack.workspace import require_workspace_binding
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.service import OccService
from sandbox.overlay.capture.types import read_output_ref
from sandbox.runtime.async_bridge import run_sync_in_executor
from sandbox.runtime.clients.layer_stack import LayerStackClient
from sandbox.runtime.clients.occ import OCCClient, RuntimeWorkspaceBindingReader
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


_SERVICE_CACHE: dict[
    str,
    tuple[
        WorkspaceLeaseClient,
        OCCMutationClient,
        "SnapshotGitignoreOracle",
        Path,
    ],
] = {}


def _services_cache_clear() -> None:
    """Drop command-exec runtime service cache. Test helper."""
    _SERVICE_CACHE.clear()


def drop_services_cache(layer_stack_root: str) -> None:
    """Drop cached command-exec services for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    _SERVICE_CACHE.pop(root, None)
    _SERVICE_CACHE.pop(str(Path(root).resolve(strict=False)), None)


async def shell(args: dict[str, object]) -> dict[str, object]:
    layer_stack, occ_client, gitignore, storage_root = _services(args)
    result = await _execute_shell(
        args,
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        storage_root=storage_root,
    )
    return _payload_from_result(result)


async def shell_batch(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    items = args.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ValueError("items must be a list of shell request objects")
    max_concurrency = max(1, _int(args.get("max_concurrency"), default=32))
    layer_stack, occ_client, gitignore, storage_root = _services(args)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_one(index: int, item: object) -> dict[str, object]:
        if not isinstance(item, Mapping):
            raise ValueError(f"batch item {index} must be an object")
        item_args = dict(args)
        item_args.pop("items", None)
        item_args.pop("max_concurrency", None)
        item_args.update(dict(item))
        wait_start = time.perf_counter()
        async with semaphore:
            run_start = time.perf_counter()
            result = await _execute_shell(
                item_args,
                layer_stack=layer_stack,
                occ_client=occ_client,
                gitignore=gitignore,
                storage_root=storage_root,
            )
        payload = _payload_from_result(result)
        timings = payload.get("timings")
        if not isinstance(timings, dict):
            timings = {}
        payload["timings"] = {
            **timings,
            "api.shell_batch.item_wait_s": run_start - wait_start,
            "api.shell_batch.item_total_s": time.perf_counter() - wait_start,
        }
        payload["batch_index"] = index
        return payload

    results = await asyncio.gather(
        *(run_one(index, item) for index, item in enumerate(items))
    )
    return {
        "success": all(bool(result.get("success", False)) for result in results),
        "results": results,
        "warnings": [],
        "timings": {
            "api.shell_batch.total_s": time.perf_counter() - total_start,
            "api.shell_batch.count": float(len(results)),
            "api.shell_batch.max_concurrency": float(max_concurrency),
        },
    }


async def _execute_shell(
    args: Mapping[str, object],
    *,
    layer_stack: WorkspaceLeaseClient,
    occ_client: OCCMutationClient,
    gitignore: "SnapshotGitignoreOracle",
    storage_root: Path,
) -> CommandExecResult:
    total_start = time.perf_counter()
    request = _command_request(args)
    run_dir = _run_dir(storage_root, request.request_id)
    timings: dict[str, float] = {}

    lease_start = time.perf_counter()
    lease = layer_stack.prepare_workspace_snapshot(
        workspace_ref=request.workspace_ref,
        request_id=request.request_id,
        cache_policy=_snapshot_cache_policy(args),
    )
    timings.update(
        {
            **lease.timings,
            "command_exec.prepare_snapshot_s": time.perf_counter() - lease_start,
        }
    )

    released = False
    try:
        barrier = _barrier(args)
        if barrier is not None:
            barrier_start = time.perf_counter()
            await _wait_file_barrier(
                storage_root,
                barrier_id=barrier[0],
                parties=barrier[1],
            )
            timings["command_exec.test_barrier_wait_s"] = (
                time.perf_counter() - barrier_start
            )

        spec = WorkspaceReplacementMountSpec(
            workspace_root=request.workspace_root,
            lowerdir=lease.lowerdir,
            upperdir=str(run_dir / "upper"),
            workdir=str(run_dir / "work"),
            manifest_version=lease.manifest_version,
            lease_id=lease.lease_id,
        )
        process = await run_sync_in_executor(
            run_workspace_replaced_command,
            spec=spec,
            request=request,
            run_dir=run_dir,
            timings=timings,
        )

        capture_start = time.perf_counter()
        path_changes = tuple(
            capture_workspace_upperdir(
                spec=spec,
                snapshot_manifest=lease.manifest,
                mounted_workspace_root=process.mounted_workspace_root,
                copy_backed=process.mount_mode == "copy_backed",
                timings=timings,
            )
        )
        timings["command_exec.capture_upperdir_s"] = (
            time.perf_counter() - capture_start
        )

        occ_start = time.perf_counter()
        changeset = await _apply_workspace_capture(
            path_changes,
            occ_client=occ_client,
            snapshot=lease.manifest,
            request=request,
        )
        timings["command_exec.occ_apply_s"] = time.perf_counter() - occ_start
        release_start = time.perf_counter()
        layer_stack.release_lease(
            workspace_ref=request.workspace_ref,
            lease_id=lease.lease_id,
        )
        released = True
        _drop_transient_lowerdir(lease)
        timings["command_exec.release_snapshot_s"] = (
            time.perf_counter() - release_start
        )
        timings = {
            **timings,
            **changeset.timings,
            **_gitignore_timings(gitignore),
        }
        timings["api.shell.overlay_s"] = (
            timings.get("command_exec.mount_workspace_s", 0.0)
            + timings.get("command_exec.run_command_s", 0.0)
            + timings.get("command_exec.capture_upperdir_s", 0.0)
        )
        timings["api.shell.occ_apply_s"] = timings["command_exec.occ_apply_s"]
        timings["command_exec.total_s"] = time.perf_counter() - total_start
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
            release_start = time.perf_counter()
            layer_stack.release_lease(
                workspace_ref=request.workspace_ref,
                lease_id=lease.lease_id,
            )
            _drop_transient_lowerdir(lease)
            timings["command_exec.release_snapshot_s"] = (
                time.perf_counter() - release_start
            )


async def _apply_workspace_capture(
    path_changes: Sequence[object],
    *,
    occ_client: OCCMutationClient,
    snapshot: object,
    request: CommandExecRequest,
) -> ChangesetResult:
    typed_changes = workspace_changes_to_occ_changes(path_changes)  # type: ignore[arg-type]
    if not typed_changes:
        return ChangesetResult(
            files=(),
            timings={},
            published_manifest_version=None,
        )
    result = await occ_client.apply_changeset(
        typed_changes,
        snapshot=snapshot,
        options=CommitOptions(
            atomic=True,
            caller_id=request.actor_id,
            description=request.description,
        ),
        workspace_ref=request.workspace_ref,
    )
    if isinstance(result, PreparedChangeset):
        raise TypeError("command-exec OCC client returned an uncommitted changeset")
    return result


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
        "conflict": _conflict_to_dict(conflict),
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


def _services(
    args: Mapping[str, object],
) -> tuple[
    WorkspaceLeaseClient,
    OCCMutationClient,
    "SnapshotGitignoreOracle",
    Path,
]:
    layer_stack_root = _layer_stack_root(args)
    cached = _SERVICE_CACHE.get(layer_stack_root)
    if cached is not None:
        return cached
    manager = get_layer_stack_manager(layer_stack_root)
    layer_stack = LayerStackClient(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    occ_service = OccService(
        gitignore=gitignore,
        layer_stack=layer_stack,
        workspace_ref=layer_stack_root,
    )
    services = cast(
        tuple[
            WorkspaceLeaseClient,
            OCCMutationClient,
            "SnapshotGitignoreOracle",
            Path,
        ],
        (
            layer_stack,
            OCCClient(
                occ_service,
                binding_reader=RuntimeWorkspaceBindingReader(),
                workspace_ref=layer_stack_root,
            ),
            gitignore,
            layer_stack.storage_root,
        ),
    )
    _SERVICE_CACHE[layer_stack_root] = services
    return services


def _command_request(args: Mapping[str, object]) -> CommandExecRequest:
    command = args.get("command")
    if isinstance(command, str):
        argv: tuple[str, ...] = ("bash", "-lc", command)
    elif isinstance(command, list):
        argv = tuple(str(part) for part in command)
    else:
        raise ValueError("command must be a string or argv list")
    timeout = args.get("timeout_seconds", args.get("timeout"))
    workspace_ref = _layer_stack_root(args)
    binding = require_workspace_binding(workspace_ref)
    return CommandExecRequest(
        request_id=str(args.get("request_id") or uuid4().hex),
        workspace_ref=workspace_ref,
        workspace_root=binding.workspace_root,
        command=argv,
        cwd=str(args.get("cwd") or "."),
        env={str(k): str(v) for k, v in _mapping(args.get("env")).items()},
        timeout_seconds=_optional_float(timeout),
        actor_id=str(args.get("actor_id") or ""),
        description=str(args.get("description") or "shell"),
    )


def _layer_stack_root(args: Mapping[str, object]) -> str:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    return layer_stack_root


def _run_dir(storage_root: Path, request_id: str) -> Path:
    safe_id = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in request_id
    ).strip("-")
    run_parent = _command_exec_runtime_root(storage_root)
    return run_parent / f"{safe_id or 'request'}-{uuid4().hex[:8]}"


def _command_exec_runtime_root(storage_root: Path) -> Path:
    shm = Path("/dev/shm")
    if shm.is_dir() and os.access(shm, os.W_OK):
        root_key = "".join(
            ch if ch.isalnum() else "-"
            for ch in str(storage_root.resolve(strict=False))
        ).strip("-")
        return shm / "eos-command-exec" / (root_key[-48:] or "layer-stack")
    return storage_root / "runtime" / "command_exec"


def _snapshot_cache_policy(args: Mapping[str, object]) -> str:
    raw = str(
        args.get("snapshot_cache_policy")
        or os.environ.get("EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY")
        or "enabled"
    ).strip()
    if raw not in {"enabled", "disabled"}:
        raise ValueError(f"unsupported snapshot cache policy: {raw}")
    return raw


def _drop_transient_lowerdir(lease: object) -> None:
    if not bool(getattr(lease, "transient_lowerdir", False)):
        return
    raw = str(getattr(lease, "lowerdir", "")).strip()
    if not raw:
        return
    lowerdir = Path(raw)
    shutil.rmtree(lowerdir.parent, ignore_errors=True)


def _barrier(args: Mapping[str, object]) -> tuple[str, int] | None:
    barrier_id = str(args.get("barrier_id") or "").strip()
    if not barrier_id:
        return None
    return barrier_id, max(1, _int(args.get("barrier_parties"), default=1))


async def _wait_file_barrier(
    storage_root: Path,
    *,
    barrier_id: str,
    parties: int,
) -> None:
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in barrier_id)
    barrier_dir = storage_root / "runtime" / "barriers" / safe_id
    barrier_dir.mkdir(parents=True, exist_ok=True)
    (barrier_dir / f"{uuid4().hex}.arrived").write_text("", encoding="utf-8")
    deadline = time.monotonic() + 10
    while len(list(barrier_dir.glob("*.arrived"))) < parties:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"runtime barrier timed out: {barrier_id}")
        await asyncio.sleep(0.05)


def _gitignore_timings(
    gitignore: "SnapshotGitignoreOracle",
) -> dict[str, float]:
    return {
        "gitignore.cache_hits_total": float(gitignore.cache_hits),
        "gitignore.cache_misses_total": float(gitignore.cache_misses),
        "gitignore.materialize_snapshot_s": float(gitignore.last_materialize_s),
        "gitignore.git_init_s": float(gitignore.last_git_init_s),
    }


def _conflict_to_dict(conflict: object | None) -> dict[str, object] | None:
    if conflict is None:
        return None
    return {
        "reason": getattr(conflict, "reason", ""),
        "conflict_file": getattr(conflict, "conflict_file", None),
        "message": getattr(conflict, "message", ""),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


__all__ = [
    "drop_services_cache",
    "shell",
    "shell_batch",
]
