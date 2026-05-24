"""Daemon-owned overlay lifecycle and publish boundary."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
from typing import AsyncIterator, Protocol
from uuid import uuid4

from sandbox.ephemeral_workspace.shell_contract import (
    ChangesetResultLike,
    CommandExecRequest,
    CommandExecResult,
    OCCMutationClient,
    SnapshotManifest,
    WorkspaceCapturePublishResult,
    WorkspaceLeaseClient,
)
from sandbox.ephemeral_workspace._execute_command import execute_command
from sandbox.overlay.capability import new_mount_api_supported
from sandbox.overlay.capture import walk_upperdir
from sandbox.overlay.kernel_mount import (
    mount_overlay,
    umount,
    validate_mount_inputs,
)
from sandbox.overlay.scratch import command_exec_scratch_root
from sandbox.overlay.path_change import OverlayPathChange
from sandbox.layer_stack.manifest import manifest_root_hash
from sandbox.layer_stack.paths import TRANSIENT_LOWERDIR_DIR
from sandbox.occ.changeset import (
    ChangesetResult,
    CommitOptions,
    DeleteChange,
    WriteChange,
    WritePayload,
)
from sandbox.occ.overlay_change_conversion import overlay_path_changes_to_occ_changes
from sandbox._shared.clock import monotonic_now
from sandbox.occ.gitignore import SnapshotGitignoreOracle
from sandbox.daemon.occ_backend import build_occ_backend
from sandbox.daemon.request_context import require_layer_stack_root
from sandbox.daemon.result_projection import (
    conflict_and_status,
    conflict_to_dict,
    gitignore_cache_timings,
    published_paths,
)
from sandbox.ephemeral_workspace.events import (
    PathChange,
    EphemeralPipelineEventBus,
    WorkspaceChangeEvent,
)
from sandbox.layer_stack.workspace_binding import (
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)


class OverlayLayerStackClient(Protocol):
    storage_root: Path

    def read_active_manifest(self) -> SnapshotManifest: ...

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> object: ...

    def release_lease(self, *, lease_id: str) -> bool: ...

    def flush_to_workspace(
        self,
        *,
        workspace_root: str | Path,
        timings: dict[str, float] | None = None,
    ) -> SnapshotManifest: ...


@dataclass(frozen=True)
class _OverlaySnapshot:
    lease_id: str
    manifest: SnapshotManifest
    layer_paths: tuple[Path, ...]


@dataclass
class OperationOverlayHandle:
    """Daemon-owned lease plus private upper/work dirs for one operation."""

    lease_id: str
    manifest_key: str
    manifest_version: int
    root_hash: str
    manifest: SnapshotManifest
    workspace_root: str
    run_dir: str
    upperdir: str
    workdir: str
    lowerdir: str | None
    layer_paths: tuple[str, ...] | None
    _overlay: EphemeralPipeline
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._overlay.release_operation_overlay(self)

    @property
    def released(self) -> bool:
        return self._released


class EphemeralPipeline:
    """Facade hiding overlay freshness, capture, and OCC behind the daemon boundary.

    The current implementation still receives a per-command upperdir from the
    command runner. The ownership boundary is the important part: callers ask
    the daemon overlay service to refresh or publish, and this class owns
    manifest recency, conversion, OCC options, and post-publish maintenance.
    """

    def __init__(
        self,
        *,
        occ_client: OCCMutationClient,
        workspace_ref: str,
        layer_stack: OverlayLayerStackClient | None = None,
        workspace_root: str = "/testbed",
        event_bus: EphemeralPipelineEventBus | None = None,
    ) -> None:
        self._occ_client = occ_client
        self._workspace_ref = workspace_ref
        self._layer_stack = layer_stack
        self._workspace_root = workspace_root.rstrip("/") or "/"
        self.event_bus = event_bus or EphemeralPipelineEventBus()
        self._active_manifest_key = ""
        self._active_manifest_version = 0
        self._mounted = False
        self._active_lease_id = ""
        self._operation_lock = asyncio.Lock()
        self._foreign_watch_task: asyncio.Task[None] | None = None
        # Lease IDs that have already been released via this overlay. Lets
        # cancel + reap fan-in on background shell jobs (cf. shell_job.py).
        self._released_lease_ids: set[str] = set()
        storage_root = (
            layer_stack.storage_root if layer_stack is not None else Path("/var/run/eos")
        )
        self._scratch_root = command_exec_scratch_root(Path(storage_root))
        self._runtime_dir_path = (
            self._scratch_root
            / "runtime"
            / "sandbox-overlay"
            / self._runtime_key(workspace_ref, self._workspace_root)
        )
        self._upperdir = self._runtime_dir / "upper"
        self._workdir = self._runtime_dir / "work"
        if layer_stack is not None and hasattr(layer_stack, "read_active_manifest"):
            self._mark_active(layer_stack.read_active_manifest())

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    @property
    def is_mounted(self) -> bool:
        return self._mounted

    @property
    def upperdir(self) -> Path:
        return self._upperdir

    @property
    def scratch_root(self) -> Path:
        return self._scratch_root

    @property
    def runtime_dir(self) -> Path:
        return self._runtime_dir

    @asynccontextmanager
    async def workspace_operation(
        self,
        *,
        reason: str = "operation",
    ) -> AsyncIterator[SnapshotManifest]:
        async with self._operation_lock:
            await self.ensure_current(reason=reason)
            yield self.current_manifest()

    def active_manifest_key(self) -> str:
        if self._layer_stack is None:
            return self._active_manifest_key
        manifest = self._layer_stack.read_active_manifest()
        self._mark_active(manifest)
        return self._active_manifest_key

    def acquire_operation_overlay(
        self,
        *,
        request_id: str,
        workspace_root: str | None = None,
    ) -> OperationOverlayHandle:
        """Lease the latest snapshot and allocate a private overlay upperdir."""
        if self._layer_stack is None:
            raise RuntimeError("acquire_operation_overlay requires layer_stack")
        run_dir = (
            self._scratch_root
            / "runtime"
            / "sandbox-overlay-ops"
            / self._runtime_key(self._workspace_ref, self._workspace_root)
            / f"{_safe_request_part(request_id)}-{uuid4().hex[:8]}"
        )
        upperdir = run_dir / "upper"
        workdir = run_dir / "work"
        snapshot = self._layer_stack.prepare_workspace_snapshot(
            request_id=request_id,
        )
        lease_id = str(getattr(snapshot, "lease_id"))
        try:
            upperdir.mkdir(parents=True, exist_ok=True)
            workdir.mkdir(parents=True, exist_ok=True)
            manifest = getattr(snapshot, "manifest")
            manifest_version = int(getattr(snapshot, "manifest_version"))
            root_hash = str(getattr(snapshot, "root_hash"))
            return OperationOverlayHandle(
                lease_id=lease_id,
                manifest_key=f"{root_hash}@{manifest_version}",
                manifest_version=manifest_version,
                root_hash=root_hash,
                manifest=manifest,
                workspace_root=str(workspace_root or self.workspace_root).rstrip("/")
                or "/",
                run_dir=run_dir.as_posix(),
                upperdir=upperdir.as_posix(),
                workdir=workdir.as_posix(),
                lowerdir=getattr(snapshot, "lowerdir", None),
                layer_paths=getattr(snapshot, "layer_paths", None),
                _overlay=self,
            )
        except Exception:
            self._release_lease(lease_id)
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    def release_operation_overlay(self, handle: OperationOverlayHandle) -> None:
        """Release a per-operation overlay lease and remove operation scratch."""
        self._release_lease(handle.lease_id)
        _drop_transient_lowerdir(
            handle.lowerdir,
            storage_root=self._layer_stack.storage_root if self._layer_stack else None,
            scratch_root=self._scratch_root,
        )
        shutil.rmtree(handle.run_dir, ignore_errors=True)

    def current_manifest(self) -> SnapshotManifest:
        if self._layer_stack is None:
            raise RuntimeError("EphemeralPipeline.current_manifest requires layer_stack")
        manifest = self._layer_stack.read_active_manifest()
        self._mark_active(manifest)
        return manifest

    async def start(self) -> None:
        """Mount the daemon-owned overlay at the workspace root."""
        if self._layer_stack is None:
            raise RuntimeError("EphemeralPipeline.start requires layer_stack")
        if self._mounted:
            return
        self._mount_active(reason="start")
        self._start_foreign_publish_watcher()

    async def stop(self) -> None:
        """Detach the daemon-owned overlay and remove scratch dirs."""
        await self._stop_foreign_publish_watcher()
        umount(Path(self.workspace_root))
        self._mounted = False
        self._release_lease(self._active_lease_id)
        self._active_lease_id = ""
        shutil.rmtree(self._runtime_dir, ignore_errors=True)

    async def ensure_current(self, *, reason: str = "ensure_current") -> str:
        """Refresh daemon-owned overlay state to the latest manifest if needed.

        The persistent mount implementation will rotate leases/remount here.
        Today this method is still valuable as the single tool-call entry gate:
        plugin and command handlers ask the daemon overlay service for a fresh
        view without importing layer-stack internals themselves.
        """
        if self._layer_stack is None:
            return self._active_manifest_key
        old_version = self._active_manifest_version
        manifest = self._layer_stack.read_active_manifest()
        new_key = self._manifest_key(manifest)
        if new_key == self._active_manifest_key:
            return self._active_manifest_key
        if self._mounted:
            manifest = self._remount_active(reason=reason)
        else:
            self._mark_active(manifest)
        self.event_bus.emit(
            WorkspaceChangeEvent(
                reason="foreign_publish" if reason != "start" else "remount",
                from_version=old_version,
                to_version=manifest.version,
                changes=(),
            )
        )
        return self._active_manifest_key

    async def publish_cycle(
        self,
        *,
        request: CommandExecRequest,
        upperdir: str | Path,
        snapshot: SnapshotManifest,
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult:
        return await self._publish_upperdir(
            upperdir=upperdir,
            snapshot=snapshot,
            workspace_ref=request.workspace_ref,
            timing_prefix="command_exec",
            reason="publish",
            run_maintenance=run_maintenance,
        )

    async def publish_pending_changes(
        self,
        *,
        snapshot: SnapshotManifest,
        reason: str = "publish",
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult:
        """Capture and publish the persistent overlay upperdir."""
        return await self._publish_upperdir(
            upperdir=self._upperdir,
            snapshot=snapshot,
            workspace_ref=self._workspace_ref,
            timing_prefix="overlay",
            reason=reason,
            run_maintenance=run_maintenance,
        )

    async def flush_to_workspace(self) -> dict[str, object]:
        """Publish pending upperdir edits, detach, rebuild base, and remount."""
        if self._layer_stack is None:
            raise RuntimeError("flush_to_workspace requires layer_stack")
        async with self._operation_lock:
            timings: dict[str, float] = {}
            was_mounted = self._mounted
            from_version = self._active_manifest_version
            if was_mounted:
                snapshot = self.current_manifest()
                publish = await self.publish_pending_changes(
                    snapshot=snapshot,
                    reason="flush",
                    run_maintenance=True,
                )
                timings.update(publish.timings)
                await self.stop()
            new_manifest = self._layer_stack.flush_to_workspace(
                workspace_root=self.workspace_root,
                timings=timings,
            )
            self._mark_active(new_manifest)
            if was_mounted:
                await self.start()
            self.event_bus.emit(
                WorkspaceChangeEvent(
                    reason="flush",
                    from_version=from_version,
                    to_version=self._active_manifest_version,
                    changes=(),
                )
            )
            return {
                "success": True,
                "manifest_version": self._active_manifest_version,
                "manifest_key": self._active_manifest_key,
                "timings": timings,
            }

    async def _publish_upperdir(
        self,
        *,
        upperdir: str | Path,
        snapshot: SnapshotManifest,
        workspace_ref: str,
        timing_prefix: str,
        reason: str,
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult:
        timings: dict[str, float] = {}
        capture_start = monotonic_now()
        path_changes = walk_upperdir(upperdir, timings=timings)
        timings[f"{timing_prefix}.capture_upperdir_s"] = (
            monotonic_now() - capture_start
        )

        occ_start = monotonic_now()
        changeset = await self._apply_workspace_capture(
            path_changes,
            snapshot=snapshot,
            workspace_ref=workspace_ref,
            run_maintenance=False,
        )
        timings[f"{timing_prefix}.occ_apply_s"] = monotonic_now() - occ_start
        maintenance_timings: dict[str, float] = {}
        old_version = getattr(snapshot, "version", self._active_manifest_version)
        if changeset.published_manifest_version is not None and run_maintenance:
            maintenance_timings = await self.run_maintenance_after_publish(
                changeset,
                workspace_ref=workspace_ref,
            )
        elif changeset.published_manifest_version is not None and self._mounted:
            self._remount_active(reason=reason)
        elif (
            changeset.published_manifest_version is not None
            and self._layer_stack is not None
            and hasattr(self._layer_stack, "read_active_manifest")
        ):
            self._mark_active(self._layer_stack.read_active_manifest())
        elif changeset.published_manifest_version is not None:
            self._active_manifest_version = int(changeset.published_manifest_version)
            self._active_manifest_key = f"unknown@{self._active_manifest_version}"
        if path_changes:
            self.event_bus.emit(
                WorkspaceChangeEvent(
                    reason=reason if reason in {"publish", "flush"} else "publish",
                    from_version=int(old_version),
                    to_version=self._active_manifest_version
                    or int(changeset.published_manifest_version or old_version),
                    changes=tuple(_event_path_change(change) for change in path_changes),
                )
            )
        return WorkspaceCapturePublishResult(
            path_changes=path_changes,
            changeset=changeset,
            timings={**timings, **maintenance_timings},
        )

    async def publish_workspace_paths(
        self,
        *,
        paths: Sequence[str],
        actor_id: str = "",
        description: str = "plugin workspace edit",
    ) -> ChangesetResult:
        """Publish direct writes made under the daemon overlay workspace root."""
        del actor_id, description
        if self._mounted:
            snapshot = self.current_manifest()
            publish = await self.publish_pending_changes(
                snapshot=snapshot,
                reason="publish",
                run_maintenance=True,
            )
            return publish.changeset
        if self._layer_stack is None:
            raise RuntimeError("publish_workspace_paths requires layer_stack")
        snapshot = self._layer_stack.read_active_manifest()
        old_version = int(getattr(snapshot, "version", self._active_manifest_version))
        changes = []
        event_changes: list[PathChange] = []
        for path in paths:
            rel = self._relative_workspace_path(path)
            full_path = Path(self.workspace_root) / rel
            if full_path.exists() or full_path.is_symlink():
                changes.append(
                    WriteChange(
                        path=rel,
                        source="overlay_capture",
                        payload=WritePayload(content_path=full_path.as_posix()),
                    )
                )
                event_changes.append(
                    PathChange(path=rel, kind="write", existed_before=True)
                )
            else:
                changes.append(DeleteChange(path=rel, source="overlay_capture"))
                event_changes.append(
                    PathChange(path=rel, kind="delete", existed_before=True)
                )
        if not changes:
            return ChangesetResult(
                files=(),
                timings={},
                published_manifest_version=None,
            )
        result = await self._occ_client.apply_changeset(
            tuple(changes),
            snapshot=snapshot,
            options=CommitOptions(atomic=len({change.path for change in changes}) > 1),
            workspace_ref=self._workspace_ref,
            run_maintenance=False,
        )
        await self.run_maintenance_after_publish(result, workspace_ref=self._workspace_ref)
        if result.published_manifest_version is not None:
            self._mark_active(self._layer_stack.read_active_manifest())
            self.event_bus.emit(
                WorkspaceChangeEvent(
                    reason="publish",
                    from_version=old_version,
                    to_version=self._active_manifest_version,
                    changes=tuple(event_changes),
                )
            )
        return result

    async def run_maintenance_after_publish(
        self,
        result: ChangesetResultLike,
        *,
        workspace_ref: str | None = None,
    ) -> dict[str, float]:
        published = getattr(result, "published_manifest_version", None)
        if published is None:
            return await self._occ_client.run_maintenance_after_publish(
                result,
                workspace_ref=workspace_ref or self._workspace_ref,
            )
        was_mounted = self._mounted
        if was_mounted:
            self._detach_active_mount()
        try:
            return await self._occ_client.run_maintenance_after_publish(
                result,
                workspace_ref=workspace_ref or self._workspace_ref,
            )
        finally:
            if was_mounted:
                self._mount_active(reason="maintenance")
            elif self._layer_stack is not None:
                self._mark_active(self._layer_stack.read_active_manifest())

    async def _apply_workspace_capture(
        self,
        path_changes: Sequence[OverlayPathChange],
        *,
        snapshot: SnapshotManifest,
        workspace_ref: str,
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
            workspace_ref=workspace_ref,
            run_maintenance=run_maintenance,
        )

    def _mark_active(self, manifest: SnapshotManifest) -> None:
        self._active_manifest_version = int(manifest.version)
        self._active_manifest_key = self._manifest_key(manifest)

    def _manifest_key(self, manifest: SnapshotManifest) -> str:
        try:
            root_hash = manifest_root_hash(manifest)  # type: ignore[arg-type]
        except Exception:
            root_hash = "unknown"
        return f"{root_hash}@{int(manifest.version)}"

    @property
    def _runtime_dir(self) -> Path:
        return self._runtime_dir_path

    def _runtime_key(self, workspace_ref: str, workspace_root: str) -> str:
        raw = f"{workspace_ref}\0{workspace_root}".encode("utf-8", "surrogateescape")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _prepare_mount_dirs(self) -> None:
        self._upperdir.mkdir(parents=True, exist_ok=True)
        self._workdir.mkdir(parents=True, exist_ok=True)

    def _remount_active(self, *, reason: str) -> SnapshotManifest:
        self._detach_active_mount()
        return self._mount_active(reason=reason)

    def _detach_active_mount(self) -> None:
        if not self._mounted:
            return
        umount(Path(self.workspace_root))
        self._mounted = False
        self._release_lease(self._active_lease_id)
        self._active_lease_id = ""
        shutil.rmtree(self._upperdir, ignore_errors=True)
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _mount_active(self, *, reason: str) -> SnapshotManifest:
        snapshot = self._prepare_overlay_snapshot(f"sandbox-overlay-{reason}")
        self._prepare_mount_dirs()
        try:
            self._mount_layer_paths(snapshot.layer_paths)
        except Exception:
            self._release_lease(snapshot.lease_id)
            raise
        self._active_lease_id = snapshot.lease_id
        self._mounted = True
        self._mark_active(snapshot.manifest)
        return snapshot.manifest

    def _mount_layer_paths(self, layer_paths: tuple[Path, ...]) -> None:
        if self._layer_stack is None:
            raise RuntimeError("mount requires layer_stack")
        mount_inputs = validate_mount_inputs(
            workspace_root=Path(self.workspace_root),
            layer_paths=layer_paths,
            upperdir=self._upperdir,
            workdir=self._workdir,
        )
        try:
            mount_overlay(
                workspace_root=mount_inputs.workspace_root,
                layer_paths=mount_inputs.layer_paths,
                upperdir=mount_inputs.upperdir,
                workdir=mount_inputs.workdir,
                pass_fds=mount_inputs.fds,
            )
        finally:
            mount_inputs.close()

    def _prepare_overlay_snapshot(self, request_id: str) -> _OverlaySnapshot:
        if self._layer_stack is None:
            raise RuntimeError("snapshot requires layer_stack")
        snapshot = self._layer_stack.prepare_workspace_snapshot(
            request_id=request_id,
        )
        raw_paths = getattr(snapshot, "layer_paths", None)
        lease_id = str(getattr(snapshot, "lease_id", ""))
        if raw_paths is None:
            self._release_lease(lease_id)
            raise RuntimeError("overlay snapshot did not provide layer paths")
        return _OverlaySnapshot(
            lease_id=lease_id,
            manifest=getattr(snapshot, "manifest"),
            layer_paths=tuple(Path(path) for path in raw_paths),
        )

    def _release_lease(self, lease_id: str) -> None:
        if not lease_id or self._layer_stack is None:
            return
        # Idempotency guard: shell background jobs route cancel + reap through
        # `_release_lease` from independent threads. A second release on the
        # same lease must silently no-op so we keep the daemon's
        # ``lease_acquire_count == lease_release_count`` AC-5 invariant.
        if lease_id in self._released_lease_ids:
            return
        self._released_lease_ids.add(lease_id)
        self._layer_stack.release_lease(lease_id=lease_id)

    def _relative_workspace_path(self, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("workspace path must not be empty")
        full = Path(raw)
        root = Path(self.workspace_root)
        if not full.is_absolute():
            return full.as_posix().strip("/")
        try:
            return full.resolve(strict=False).relative_to(
                root.resolve(strict=False)
            ).as_posix()
        except ValueError:
            raise ValueError(f"path is outside workspace root: {path}") from None

    def _start_foreign_publish_watcher(self) -> None:
        if self._foreign_watch_task is not None and not self._foreign_watch_task.done():
            return
        self._foreign_watch_task = asyncio.create_task(
            self._watch_foreign_publishes()
        )

    async def _stop_foreign_publish_watcher(self) -> None:
        task = self._foreign_watch_task
        self._foreign_watch_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _watch_foreign_publishes(self) -> None:
        interval = _foreign_watch_interval_s()
        while True:
            await asyncio.sleep(interval)
            if not self._mounted:
                return
            async with self._operation_lock:
                await self.ensure_current(reason="foreign_watch")


def _foreign_watch_interval_s() -> float:
    raw = os.environ.get("EOS_OVERLAY_FOREIGN_WATCH_INTERVAL_S", "").strip()
    if not raw:
        return 0.25
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 0.25


def _event_path_change(change: object) -> PathChange:
    if hasattr(change, "path") and hasattr(change, "kind"):
        return PathChange.from_overlay_change(change)  # type: ignore[arg-type]
    return PathChange(path=str(change), kind="write", existed_before=False)


def _drop_transient_lowerdir(
    lowerdir_raw: str | None,
    *,
    storage_root: Path | None,
    scratch_root: Path,
) -> None:
    if not lowerdir_raw:
        return
    lowerdir = Path(str(lowerdir_raw))
    scratch_dir = lowerdir.parent
    transient_roots = {
        (scratch_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(strict=False),
    }
    if storage_root is not None:
        transient_roots.add(
            (storage_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(strict=False)
        )
    if (
        lowerdir.name != "lower"
        or scratch_dir.parent.name != TRANSIENT_LOWERDIR_DIR
        or scratch_dir.parent.resolve(strict=False) not in transient_roots
    ):
        return
    shutil.rmtree(scratch_dir, ignore_errors=True)


def _safe_request_part(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in str(value)
    ).strip("-")
    return safe or "operation"


_MAX_OVERLAYS = 256
_OVERLAYS: OrderedDict[str, EphemeralPipeline] = OrderedDict()
_LOCKS: dict[str, asyncio.Lock] = {}


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
    request = _shell_command_request(args)
    pipeline = EphemeralPipeline(
        occ_client=occ_client,
        workspace_ref=request.workspace_ref,
        layer_stack=layer_stack,
        workspace_root=request.workspace_root,
    )
    return await execute_command(
        request,
        layer_stack=layer_stack,
        capture_publisher=pipeline,
        storage_root=storage_root,
        timing_provider=lambda: gitignore_cache_timings(gitignore),
    )


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


_MAX_ARGV_BYTES = 128 * 1024


def _shell_command_request(args: Mapping[str, object]) -> CommandExecRequest:
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
    result: dict[str, str] = {}
    for key_raw, value_raw in raw.items():
        key = str(key_raw)
        value = str(value_raw)
        if not key:
            raise ValueError("env entry has empty key")
        if "\0" in key or "\0" in value:
            raise ValueError(f"env entry contains NUL byte: {key!r}")
        if "=" in key:
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


async def get_sandbox_overlay(
    layer_stack_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    start: bool = True,
) -> EphemeralPipeline:
    """Return the daemon-owned pipeline for a bound workspace."""
    key_root = Path(layer_stack_root).resolve(strict=False)
    binding = require_workspace_binding(key_root)
    effective_workspace = Path(workspace_root or binding.workspace_root)
    if effective_workspace != Path(binding.workspace_root):
        raise WorkspaceBindingError(
            "overlay workspace_root does not match workspace binding: "
            f"{effective_workspace} != {binding.workspace_root}"
        )
    key = f"{key_root.as_posix()}\0{effective_workspace.as_posix()}"
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        pipeline = _OVERLAYS.get(key)
        if pipeline is None:
            backend = build_occ_backend(key_root.as_posix())
            pipeline = EphemeralPipeline(
                occ_client=backend.occ_client,
                workspace_ref=key_root.as_posix(),
                layer_stack=backend.layer_stack,
                workspace_root=effective_workspace.as_posix(),
            )
            _OVERLAYS[key] = pipeline
            if len(_OVERLAYS) > _MAX_OVERLAYS:
                _OVERLAYS.popitem(last=False)
        else:
            _OVERLAYS.move_to_end(key)
        if start and not pipeline.is_mounted and new_mount_api_supported():
            await pipeline.start()
        return pipeline


async def stop_all_overlays() -> None:
    pipelines = list(_OVERLAYS.values())
    _OVERLAYS.clear()
    _LOCKS.clear()
    for pipeline in pipelines:
        await pipeline.stop()


async def stop_sandbox_overlay(
    layer_stack_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, object]:
    key_root = Path(layer_stack_root).resolve(strict=False)
    workspace_candidates = _workspace_unmount_candidates(key_root, workspace_root)
    cache_entries = [
        (key, pipeline)
        for key, pipeline in list(_OVERLAYS.items())
        if _cache_key_root(key) == key_root.as_posix()
    ]
    for key, _pipeline in cache_entries:
        _OVERLAYS.pop(key, None)
        _LOCKS.pop(key, None)

    warnings: list[str] = []
    stopped = 0
    for _key, pipeline in cache_entries:
        try:
            await pipeline.stop()
            stopped += 1
        except Exception as exc:  # pragma: no cover - defensive cleanup path
            warnings.append(f"{type(exc).__name__}: {exc}")

    for candidate in workspace_candidates:
        try:
            umount(candidate)
        except Exception as exc:  # pragma: no cover - defensive cleanup path
            warnings.append(f"{candidate}: {type(exc).__name__}: {exc}")

    return {
        "success": True,
        "workspace_roots": [path.as_posix() for path in workspace_candidates],
        "stopped_overlays": stopped,
        "warnings": warnings,
    }


def _workspace_unmount_candidates(
    layer_stack_root: Path,
    workspace_root: str | Path | None,
) -> list[Path]:
    candidates: list[Path] = []
    if workspace_root is not None and str(workspace_root).strip():
        candidates.append(Path(workspace_root))
    binding = read_workspace_binding(layer_stack_root)
    if binding is not None:
        candidates.append(Path(binding.workspace_root))
    return _dedupe_paths(candidates)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _cache_key_root(key: str) -> str:
    root, _, _workspace = key.partition("\0")
    return root


def clear_overlay_manager_for_tests() -> None:
    _OVERLAYS.clear()
    _LOCKS.clear()


__all__ = [
    "OperationOverlayHandle",
    "EphemeralPipeline",
    "OverlayLayerStackClient",
    "clear_overlay_manager_for_tests",
    "execute_shell_api",
    "get_sandbox_overlay",
    "stop_all_overlays",
    "stop_sandbox_overlay",
]
