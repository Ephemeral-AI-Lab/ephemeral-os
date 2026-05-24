"""Per-operation overlay handle helpers for EphemeralPipeline."""

from __future__ import annotations

import asyncio
import shutil
from uuid import uuid4

from sandbox._shared.models import ToolCallResult
from sandbox._shared.resource_audit import command_exec_resource_timings
from sandbox.ephemeral_workspace._types import OperationOverlayHandle
from sandbox.ephemeral_workspace._utils import (
    _drop_transient_lowerdir,
    safe_request_part,
)
from sandbox.overlay import lifecycle as overlay_lifecycle
from sandbox.overlay.handle import OverlayHandle


class EphemeralOperationMixin:
    def _attach_resource_timings(
        self,
        result: ToolCallResult,
        *,
        handle: OverlayHandle,
        changed_path_count: int,
    ) -> ToolCallResult:
        if self._layer_stack is None:
            return result
        payload = dict(result)
        timings = dict(
            payload.get("timings") if isinstance(payload.get("timings"), dict) else {}
        )
        timings.update(
            command_exec_resource_timings(
                storage_root=self._layer_stack.storage_root,
                scratch_root=self._scratch_root,
                run_dir=handle.upperdir.parent,
                upperdir=handle.upperdir,
                manifest=handle.snapshot_manifest,
                changed_path_count=changed_path_count,
            )
        )
        payload["timings"] = timings
        return payload

    def _lock_for(self, handle: OverlayHandle) -> asyncio.Lock:
        lock = self._handle_locks.get(handle.lease_id)
        if lock is None:
            lock = self._handle_locks[handle.lease_id] = asyncio.Lock()
        return lock

    async def _destroy_with_lease_guard(self, handle: OverlayHandle) -> None:
        async with self._lock_for(handle):
            if handle._destroyed:
                self._handle_locks.pop(handle.lease_id, None)
                return
            if handle.lease_id and handle.lease_id in self._released_lease_ids:
                handle._destroyed = True
                self._handle_locks.pop(handle.lease_id, None)
                return
            if handle.lease_id:
                self._released_lease_ids.add(handle.lease_id)
            try:
                await overlay_lifecycle.destroy(handle)
            finally:
                self._handle_locks.pop(handle.lease_id, None)

    def acquire_operation_overlay(
        self,
        *,
        invocation_id: str,
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
            / f"{safe_request_part(invocation_id)}-{uuid4().hex[:8]}"
        )
        upperdir = run_dir / "upper"
        workdir = run_dir / "work"
        snapshot = self._layer_stack.prepare_workspace_snapshot(
            request_id=invocation_id,
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


__all__ = ["EphemeralOperationMixin"]
