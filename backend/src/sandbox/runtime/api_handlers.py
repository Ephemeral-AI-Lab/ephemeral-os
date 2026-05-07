"""Runtime-local handlers for guarded sandbox API operations."""

from __future__ import annotations

import asyncio
import fcntl
import io
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from sandbox.api.tool.result_projection import (
    committed_paths,
    conflict_and_status,
)
from sandbox.layer_stack import LayerStackManager
from sandbox.layer_stack.manifest import manifest_path
from sandbox.layer_stack.workspace import (
    WorkspaceBinding,
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)
from sandbox.occ.changeset.builders import build_api_edit_change, build_api_write_change
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.service import OccService
from sandbox.runtime.clients.layer_stack import LayerStackClient
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


_PROCESS_COMMIT_BUCKETS = 16
"""Phase 4 — number of hashed asyncio.Lock buckets per ``layer_stack_root``.

Each prepared changeset acquires the buckets that hash from its changed
paths (sorted to prevent deadlock when one op spans multiple buckets).
Disjoint-path commits land in different buckets so they no longer
serialize on a single in-process Lock; the underlying ``OccSerialMerger``
batches them via its 2 ms batch window.
"""

_PROCESS_COMMIT_LOCK_BUCKETS: dict[str, tuple[asyncio.Lock, ...]] = {}

_SERVICE_CACHE: dict[str, tuple[LayerStackManager, OccService, "SnapshotGitignoreOracle"]] = {}


def _services_cache_clear() -> None:
    """Drop the per-``layer_stack_root`` service cache. Test helper."""
    _SERVICE_CACHE.clear()
    from sandbox.runtime import command_exec_server

    command_exec_server._services_cache_clear()


def drop_services_cache(layer_stack_root: str) -> None:
    """Drop cached runtime services for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    _SERVICE_CACHE.pop(root, None)
    _SERVICE_CACHE.pop(str(Path(root).resolve(strict=False)), None)
    from sandbox.runtime import command_exec_server

    command_exec_server.drop_services_cache(root)


async def _prepare_changeset(
    occ_service: OccService,
    *,
    changes: Sequence[Change],
    snapshot: object = None,
    options: CommitOptions | None = None,
) -> PreparedChangeset:
    """Prepare a changeset in the resident runtime process."""
    return await occ_service.prepare_changeset(
        changes,
        snapshot=snapshot,  # type: ignore[arg-type]
        options=options,
    )


async def write_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    _, occ_service, gitignore = _services(args)
    path = _workspace_layer_path(args, str(args.get("path") or ""))
    change = build_api_write_change(
        path=path,
        final_content=str(args.get("content") or ""),
        create_only=not bool(args.get("overwrite", True)),
    )
    layer_stack_root = str(args["layer_stack_root"])
    layer_root = Path(layer_stack_root)
    prepare_start = time.perf_counter()
    prepared = await _prepare_changeset(
        occ_service,
        changes=[change],
        options=CommitOptions(
            atomic=False,
            caller_id=str(args.get("actor_id") or ""),
            description=str(args.get("description") or f"write {path}"),
        ),
    )
    prepare_elapsed = time.perf_counter() - prepare_start
    gate_start = time.perf_counter()
    async with _process_commit_gate(layer_root, _prepared_paths(prepared)):
        gate_acquired = time.perf_counter()
        flock_start = time.perf_counter()
        async with _commit_lock(layer_root):
            flock_acquired = time.perf_counter()
            commit_start = time.perf_counter()
            result = await occ_service.commit_prepared(prepared)
            commit_elapsed = time.perf_counter() - commit_start
    conflict, status = conflict_and_status(result.files)
    return {
        "success": result.success,
        "changed_paths": list(committed_paths(result.files, fallback_path=path)),
        "status": status,
        "conflict": _conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "timings": {
            **result.timings,
            **_gitignore_timings(gitignore),
            "api.write.prepare_s": prepare_elapsed,
            "api.write.commit_s": commit_elapsed,
            "api.write.process_gate_wait_s": gate_acquired - gate_start,
            "api.write.flock_wait_s": flock_acquired - flock_start,
            "api.write.total_s": time.perf_counter() - total_start,
        },
    }


async def edit_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    _, occ_service, gitignore = _services(args)
    path = _workspace_layer_path(args, str(args.get("path") or ""))
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
    layer_stack_root = str(args["layer_stack_root"])
    layer_root = Path(layer_stack_root)
    prepare_start = time.perf_counter()
    prepared = await _prepare_changeset(
        occ_service,
        changes=changes,
        options=CommitOptions(
            atomic=False,
            caller_id=str(args.get("actor_id") or ""),
            description=str(args.get("description") or f"edit {path}"),
        ),
    )
    prepare_elapsed = time.perf_counter() - prepare_start
    gate_start = time.perf_counter()
    async with _process_commit_gate(layer_root, _prepared_paths(prepared)):
        gate_acquired = time.perf_counter()
        flock_start = time.perf_counter()
        async with _commit_lock(layer_root):
            flock_acquired = time.perf_counter()
            commit_start = time.perf_counter()
            result = await occ_service.commit_prepared(prepared)
            commit_elapsed = time.perf_counter() - commit_start
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
            **_gitignore_timings(gitignore),
            "api.edit.prepare_s": prepare_elapsed,
            "api.edit.commit_s": commit_elapsed,
            "api.edit.process_gate_wait_s": gate_acquired - gate_start,
            "api.edit.flock_wait_s": flock_acquired - flock_start,
            "api.edit.total_s": time.perf_counter() - total_start,
        },
    }


async def read_file(args: dict[str, object]) -> dict[str, object]:
    total_start = time.perf_counter()
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    binding = _require_read_binding(layer_stack_root)
    path = binding.relative_layer_path(str(args.get("path") or ""))
    manager, _, _ = _services(args)
    active = manager.read_active_manifest()
    if active.version <= 0:
        raise WorkspaceBindingError(
            f"active manifest is empty for workspace binding: {layer_stack_root}"
        )
    read_start = time.perf_counter()
    content, exists = manager.read_text(path, active)
    read_elapsed = time.perf_counter() - read_start
    return {
        "success": True,
        "exists": exists,
        "content": content,
        "encoding": "utf-8",
        "timings": {
            "api.read.layer_stack_read_s": read_elapsed,
            "api.read.total_s": time.perf_counter() - total_start,
        },
    }


async def layer_metrics(args: dict[str, object]) -> dict[str, object]:
    manager, _, _ = _services(args)
    manifest = manager.read_active_manifest()
    binding = read_workspace_binding(str(args.get("layer_stack_root") or ""))
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
        "active_leases": manager.active_lease_count(),
        "pinned_layers": len(manager.pinned_layers()),
        "layer_dirs": len(layer_dirs),
        "staging_dirs": len(staging_dirs),
        "storage_bytes": total_bytes,
        "workspace_bound": binding is not None,
        "workspace_root": binding.workspace_root if binding is not None else "",
        "base_root_hash": (
            binding.base_root_hash if binding is not None else ""
        ),
    }


def _services(args: Mapping[str, object]) -> tuple[
    LayerStackManager,
    OccService,
    "SnapshotGitignoreOracle",
]:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    cached = _SERVICE_CACHE.get(layer_stack_root)
    if cached is not None:
        return cached
    manager = get_layer_stack_manager(layer_stack_root)
    layer_stack = LayerStackClient(manager)
    gitignore = SnapshotGitignoreOracle(layer_stack)
    services = (
        manager,
        OccService(
            gitignore=gitignore,
            layer_stack=layer_stack,
        ),
        gitignore,
    )
    _SERVICE_CACHE[layer_stack_root] = services
    return services


def _require_read_binding(layer_stack_root: str) -> WorkspaceBinding:
    if not layer_stack_root:
        raise WorkspaceBindingError("layer_stack_root is required")
    binding = require_workspace_binding(layer_stack_root)
    if not manifest_path(layer_stack_root).exists():
        raise WorkspaceBindingError(
            f"active manifest is missing for workspace binding: {layer_stack_root}"
        )
    return binding


def _workspace_layer_path(args: Mapping[str, object], path: str) -> str:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    binding = read_workspace_binding(layer_stack_root) if layer_stack_root else None
    if binding is None:
        return path
    return binding.relative_layer_path(path)


def _gitignore_timings(
    gitignore: "SnapshotGitignoreOracle",
) -> dict[str, float]:
    """Per-call gitignore counters.

    The ``*_total`` suffix marks these as **cumulative since this runtime
    process started** rather than per-call. The resident daemon keeps the
    in-memory oracle across calls, so a c=16 burst will see
    ``cache_misses_total = 1`` on the first call and 0 thereafter for the
    same snapshot version. The summarizer treats these as monotonic counters,
    not gauges.
    """
    return {
        "gitignore.cache_hits_total": float(gitignore.cache_hits),
        "gitignore.cache_misses_total": float(gitignore.cache_misses),
        "gitignore.materialize_snapshot_s": float(gitignore.last_materialize_s),
        "gitignore.git_init_s": float(gitignore.last_git_init_s),
    }


def _bucket_locks(storage_root: Path) -> tuple[asyncio.Lock, ...]:
    """Return the ``_PROCESS_COMMIT_BUCKETS`` asyncio.Locks for *storage_root*.

    Locks are created lazily and reused across the process lifetime. Resolving
    the key against ``storage_root.resolve(strict=False)`` so symlinked paths
    share the same bucket set.
    """
    key = str(storage_root.resolve(strict=False))
    locks = _PROCESS_COMMIT_LOCK_BUCKETS.get(key)
    if locks is None:
        locks = tuple(asyncio.Lock() for _ in range(_PROCESS_COMMIT_BUCKETS))
        _PROCESS_COMMIT_LOCK_BUCKETS[key] = locks
    return locks


def _bucket_indices_for_paths(paths: Sequence[str] | None) -> tuple[int, ...]:
    """Pick the bucket indices a commit needs, sorted to be deadlock-free.

    A commit that touches paths in different buckets must take all of those
    locks; sorting the indices guarantees any two such commits with
    overlapping bucket sets agree on acquisition order, so they cannot
    deadlock.
    """
    if not paths:
        return (0,)
    indices = {hash(path) % _PROCESS_COMMIT_BUCKETS for path in paths}
    return tuple(sorted(indices))


class _process_commit_gate:
    """Path-bucketed asyncio.Lock gate (Phase 4).

    Replaces the prior single ``asyncio.Lock`` per ``layer_stack_root`` with
    ``_PROCESS_COMMIT_BUCKETS`` locks hashed by path. Disjoint-path commits
    take different buckets and proceed concurrently; the
    ``OccSerialMerger``'s batch window then collapses them into one publish.

    Lock acquisition is in sorted bucket-id order; release is in reverse
    order. Together those make the gate deadlock-free even when a single
    commit spans multiple buckets.
    """

    def __init__(
        self,
        storage_root: Path,
        paths: Sequence[str] | None = None,
    ) -> None:
        self._locks = _bucket_locks(storage_root)
        self._to_acquire: tuple[asyncio.Lock, ...] = tuple(
            self._locks[index]
            for index in _bucket_indices_for_paths(paths)
        )
        self._held: list[asyncio.Lock] = []

    async def __aenter__(self) -> "_process_commit_gate":
        for lock in self._to_acquire:
            await lock.acquire()
            self._held.append(lock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        while self._held:
            self._held.pop().release()


def _prepared_paths(prepared: object) -> tuple[str, ...]:
    """Extract the path set a ``PreparedChangeset`` will commit.

    Mirrors :func:`sandbox.occ.serial_merger._path_set` without taking a
    runtime dependency on the merger module's private helper. Returns a
    tuple so callers can reuse it without re-iterating ``path_groups``.
    """
    groups = getattr(prepared, "path_groups", ())
    return tuple(group.path for group in groups)


def _running_in_daemon() -> bool:
    """Return True when this handler runs inside the resident runtime daemon.

    The daemon sets ``EPHEMERALOS_RUNTIME_DAEMON=1`` on startup. In that mode
    every call into a sandbox goes through the same daemon process, so the
    in-process ``_PROCESS_COMMIT_LOCKS`` (asyncio.Lock) already serializes
    commits and the cross-process flock fence is redundant.
    """
    return os.environ.get("EPHEMERALOS_RUNTIME_DAEMON") == "1"


class _commit_lock:
    def __init__(self, storage_root: Path) -> None:
        self._path = storage_root / ".commit.lock"
        self._file: io.BufferedRandom | None = None
        self._skipped = _running_in_daemon()

    async def __aenter__(self) -> "_commit_lock":
        if self._skipped:
            return self
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


__all__ = [
    "drop_services_cache",
    "edit_file",
    "layer_metrics",
    "read_file",
    "write_file",
]
