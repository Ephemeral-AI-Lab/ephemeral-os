"""Runtime-local handlers for guarded sandbox API operations."""

from __future__ import annotations

import asyncio
import fcntl
import io
import subprocess
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from sandbox.api.tool.result_projection import (
    committed_paths,
    conflict_and_status,
    published_paths,
)
from sandbox.layer_stack import LayerStackManager
from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset.builders import build_api_edit_change, build_api_write_change
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.content.gitignore_oracle import GitignoreOracle
from sandbox.occ.overlay_capture import overlay_capture_to_occ_changes
from sandbox.occ.service import OccService
from sandbox.overlay.capture.types import OverlayCapture, read_output_ref
from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker
from sandbox.overlay.runner.snapshot_overlay_runner import OverlayShellRequest


_PROCESS_COMMIT_LOCKS: dict[str, asyncio.Lock] = {}


async def shell(args: dict[str, object]) -> dict[str, object]:
    manager, occ_service = _services(args)
    return await _shell_with_services(
        args,
        manager=manager,
        occ_service=occ_service,
    )


async def shell_batch(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    items = args.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ValueError("items must be a list of shell request objects")
    max_concurrency = max(1, _int(args.get("max_concurrency"), default=32))
    manager, occ_service = _services(args)
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
            result = await _shell_with_services(
                item_args,
                manager=manager,
                occ_service=occ_service,
            )
        timings = result.get("timings")
        if not isinstance(timings, dict):
            timings = {}
        timings = {
            **timings,
            "api.shell_batch.item_wait_s": run_start - wait_start,
            "api.shell_batch.item_total_s": time.perf_counter() - wait_start,
        }
        result["timings"] = timings
        result["batch_index"] = index
        return result

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


async def _shell_with_services(
    args: Mapping[str, object],
    *,
    manager: LayerStackManager,
    occ_service: OccService,
) -> dict[str, object]:
    total_start = time.perf_counter()
    request = _shell_request(args)

    overlay_start = time.perf_counter()
    capture = await _run_overlay(
        manager=manager,
        request=request,
        barrier=_barrier(args),
    )
    overlay_elapsed = time.perf_counter() - overlay_start

    occ_start = time.perf_counter()
    changeset = await _apply_overlay_capture(
        capture,
        occ_service=occ_service,
        caller_id=str(args.get("actor_id") or ""),
        description=str(args.get("description") or "shell"),
    )
    occ_elapsed = time.perf_counter() - occ_start

    conflict, conflict_status = conflict_and_status(changeset.files)
    command_failed = capture.exit_code != 0
    success = not command_failed and changeset.success
    status = "ok" if success else conflict_status if conflict is not None else "error"
    timings = {
        **capture.timings,
        **changeset.timings,
        "api.shell.overlay_s": overlay_elapsed,
        "api.shell.occ_apply_s": occ_elapsed,
        "api.shell.total_s": time.perf_counter() - total_start,
    }
    return {
        "success": success,
        "exit_code": capture.exit_code,
        "stdout": read_output_ref(capture.stdout_ref),
        "stderr": read_output_ref(capture.stderr_ref),
        "changed_paths": list(published_paths(changeset.files)),
        "status": status,
        "conflict": _conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "warnings": [],
        "timings": timings,
    }


async def write_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    _, occ_service = _services(args)
    path = str(args.get("path") or "")
    change = build_api_write_change(
        path=path,
        final_content=str(args.get("content") or ""),
        create_only=not bool(args.get("overwrite", True)),
    )
    layer_root = Path(str(args["layer_stack_root"]))
    async with _process_commit_gate(layer_root):
        async with _commit_lock(layer_root):
            result = await occ_service.apply_changeset(
                [change],
                options=CommitOptions(
                    caller_id=str(args.get("actor_id") or ""),
                    description=str(args.get("description") or f"write {path}"),
                ),
            )
    if isinstance(result, PreparedChangeset):
        raise TypeError("runtime write_file returned an uncommitted changeset")
    conflict, status = conflict_and_status(result.files)
    return {
        "success": result.success,
        "changed_paths": list(committed_paths(result.files, fallback_path=path)),
        "status": status,
        "conflict": _conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "timings": {
            **result.timings,
            "api.write.total_s": time.perf_counter() - total_start,
        },
    }


async def edit_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    _, occ_service = _services(args)
    path = str(args.get("path") or "")
    edits = args.get("edits")
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)):
        raise ValueError("edits must be a list of search/replace objects")
    changes: list[Change] = []
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise ValueError("each edit must be an object")
        changes.append(
            build_api_edit_change(
                path=path,
                old_text=str(edit.get("old_text") or ""),
                new_text=str(edit.get("new_text") or ""),
            )
        )
    layer_root = Path(str(args["layer_stack_root"]))
    async with _process_commit_gate(layer_root):
        async with _commit_lock(layer_root):
            result = await occ_service.apply_changeset(
                changes,
                options=CommitOptions(
                    caller_id=str(args.get("actor_id") or ""),
                    description=str(args.get("description") or f"edit {path}"),
                ),
            )
    if isinstance(result, PreparedChangeset):
        raise TypeError("runtime edit_file returned an uncommitted changeset")
    conflict, status = conflict_and_status(result.files)
    return {
        "success": result.success,
        "changed_paths": list(committed_paths(result.files, fallback_path=path)),
        "applied_edits": len(edits) if result.success else 0,
        "status": status,
        "conflict": _conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "timings": {
            **result.timings,
            "api.edit.total_s": time.perf_counter() - total_start,
        },
    }


async def read_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    manager, _ = _services(args)
    content, exists = manager.read_text(str(args.get("path") or ""))
    return {
        "success": True,
        "exists": exists,
        "content": content,
        "encoding": "utf-8",
        "timings": {"api.read.total_s": time.perf_counter() - total_start},
    }


async def pinned_layers(args: dict[str, object]) -> dict[str, object]:
    manager, _ = _services(args)
    return {
        "success": True,
        "pinned_layers": list(manager.pinned_layers()),
    }


async def layer_metrics(args: dict[str, object]) -> dict[str, object]:
    manager, _ = _services(args)
    manifest = manager.read_active_manifest()
    layer_dirs = tuple((manager.storage_root / "layers").iterdir())
    staging_dirs = tuple((manager.storage_root / "staging").iterdir())
    total_bytes = 0
    for entry in manager.storage_root.rglob("*"):
        if entry.is_file() or entry.is_symlink():
            total_bytes += entry.lstat().st_size
    return {
        "success": True,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth,
        "active_leases": len(manager.lease_snapshots()),
        "pinned_layers": len(manager.pinned_layers()),
        "layer_dirs": len(layer_dirs),
        "staging_dirs": len(staging_dirs),
        "storage_bytes": total_bytes,
    }


async def compact(args: dict[str, object]) -> dict[str, object]:
    manager, _ = _services(args)
    max_depth = max(1, _int(args.get("max_depth"), default=4))
    before = manager.read_active_manifest()
    squashed = manager.squash(max_depth=max_depth)
    gc = manager.collect_garbage(young_staging_age_seconds=0)
    after = manager.read_active_manifest()
    return {
        "success": True,
        "max_depth": max_depth,
        "before_depth": before.depth,
        "after_depth": after.depth,
        "squashed": squashed is not None,
        "orphan_layers_removed": list(gc.orphan_layers_removed),
        "orphan_staging_removed": list(gc.orphan_staging_removed),
    }


async def _run_overlay(
    *,
    manager: LayerStackManager,
    request: OverlayShellRequest,
    barrier: tuple[str, int] | None,
) -> OverlayCapture:
    total_start = time.perf_counter()
    lease_start = time.perf_counter()
    lease = manager.acquire_snapshot_lease(request.request_id)
    timings = {"overlay.lease_acquire_s": time.perf_counter() - lease_start}
    invoke_start = time.perf_counter()
    try:
        if barrier is not None:
            barrier_start = time.perf_counter()
            await _wait_file_barrier(
                manager.storage_root,
                barrier_id=barrier[0],
                parties=barrier[1],
            )
            timings["overlay.test_barrier_wait_s"] = time.perf_counter() - barrier_start
        invoke_start = time.perf_counter()
        capture = await RuntimeInvoker(storage_root=manager.storage_root).invoke(
            request=request,
            manifest=lease.manifest,
        )
    finally:
        timings["overlay.invoke_total_s"] = time.perf_counter() - invoke_start
        release_start = time.perf_counter()
        manager.release_lease(lease.lease_id)
        timings["overlay.lease_release_s"] = time.perf_counter() - release_start
        timings["overlay.runner_total_s"] = time.perf_counter() - total_start
    return OverlayCapture.from_dict(
        {
            **capture.to_dict(),
            "timings": {**capture.timings, **timings},
        }
    )


async def _apply_overlay_capture(
    capture: OverlayCapture,
    *,
    occ_service: OccService,
    caller_id: str,
    description: str,
) -> ChangesetResult:
    changes: Sequence[Change] = overlay_capture_to_occ_changes(capture)
    if not changes:
        return ChangesetResult(
            files=(),
            timings=dict(capture.timings),
            published_manifest_version=None,
        )
    if capture.snapshot_manifest is None:
        raise ValueError("overlay capture is missing its leased manifest")
    layer_root = occ_service_layer_root(occ_service)
    async with _process_commit_gate(layer_root):
        async with _commit_lock(layer_root):
            result = await occ_service.apply_changeset(
                changes,
                snapshot=capture.snapshot_manifest,
                options=CommitOptions(caller_id=caller_id, description=description),
            )
    if isinstance(result, PreparedChangeset):
        raise TypeError("runtime shell returned an uncommitted changeset")
    return result


def _services(args: Mapping[str, object]) -> tuple[LayerStackManager, OccService]:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    manager = LayerStackManager(layer_stack_root)
    gitignore = _LayerStackGitignoreOracle(manager)
    return manager, OccService(gitignore=gitignore, layer_stack=manager)


def _shell_request(args: Mapping[str, object]) -> OverlayShellRequest:
    command = args.get("command")
    if isinstance(command, str):
        argv: tuple[str, ...] = ("bash", "-lc", command)
    elif isinstance(command, list):
        argv = tuple(str(part) for part in command)
    else:
        raise ValueError("command must be a string or argv list")
    timeout = args.get("timeout_seconds", args.get("timeout"))
    return OverlayShellRequest(
        request_id=str(args.get("request_id") or uuid4().hex),
        command=argv,
        cwd=str(args.get("cwd") or "."),
        env={str(k): str(v) for k, v in _mapping(args.get("env")).items()},
        timeout_seconds=_optional_float(timeout),
    )


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

def occ_service_layer_root(service: OccService) -> Path:
    layer_stack = getattr(service, "_layer_stack", None)
    root = getattr(layer_stack, "storage_root", None)
    if root is None:
        raise RuntimeError("OccService is missing a layer stack")
    return Path(root)


def _process_commit_gate(storage_root: Path) -> asyncio.Lock:
    key = str(storage_root.resolve(strict=False))
    lock = _PROCESS_COMMIT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROCESS_COMMIT_LOCKS[key] = lock
    return lock


class _commit_lock:
    def __init__(self, storage_root: Path) -> None:
        self._path = storage_root / ".commit.lock"
        self._file: io.BufferedRandom | None = None

    async def __aenter__(self) -> "_commit_lock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("a+b")
        lock_file = self._file
        await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._file is None:
            return
        try:
            await asyncio.to_thread(fcntl.flock, self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


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


class _LayerStackGitignoreOracle(GitignoreOracle):
    """Evaluate gitignore rules from a materialized layer-stack snapshot."""

    def __init__(self, layer_stack: LayerStackManager) -> None:
        self._layer_stack = layer_stack
        self._oracles: dict[int, tuple[tempfile.TemporaryDirectory[str], GitignoreOracle]] = {}

    def is_ignored(self, path: str) -> bool:
        return self.is_ignored_in_snapshot(
            path,
            self._layer_stack.read_active_manifest(),
        )

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        snapshot = self._layer_stack.read_active_manifest()
        return {path for path in paths if self.is_ignored_in_snapshot(path, snapshot)}

    def is_ignored_in_snapshot(self, path: str, snapshot: Manifest) -> bool:
        return self._oracle_for_snapshot(snapshot).is_ignored(path)

    def _oracle_for_snapshot(self, snapshot: Manifest) -> GitignoreOracle:
        version = snapshot.version
        cached = self._oracles.get(version)
        if cached is not None:
            return cached[1]

        temp_dir = tempfile.TemporaryDirectory(prefix="eos-gitignore-")
        workspace = Path(temp_dir.name)
        self._layer_stack.materialize(workspace, snapshot)
        _init_git_workspace(workspace)
        oracle = GitignoreOracle(str(workspace))
        self._oracles[version] = (temp_dir, oracle)
        return oracle


def _init_git_workspace(workspace: Path) -> None:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "init", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git init for OCC gitignore oracle failed: {stderr!r}")


__all__ = [
    "compact",
    "edit_file",
    "layer_metrics",
    "pinned_layers",
    "read_file",
    "shell",
    "shell_batch",
    "write_file",
]
