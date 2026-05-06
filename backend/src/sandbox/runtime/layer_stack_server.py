"""Runtime-local workspace server for layer-stack base construction."""

from __future__ import annotations

import threading
from pathlib import Path

from sandbox.layer_stack.stack_manager import (
    LayerStackManager,
    PrepareWorkspaceSnapshotResult,
)
from sandbox.layer_stack.manifest import manifest_path, read_manifest
from sandbox.layer_stack.workspace_base import build_workspace_base
from sandbox.layer_stack.workspace import (
    WorkspaceBinding,
    WorkspaceBindingError,
    require_workspace_binding,
    read_workspace_binding,
)


_MANAGER_CACHE_LOCK = threading.RLock()
_MANAGER_CACHE: dict[str, LayerStackManager] = {}


def get_layer_stack_manager(layer_stack_root: str | Path) -> LayerStackManager:
    key = str(Path(layer_stack_root).resolve(strict=False))
    with _MANAGER_CACHE_LOCK:
        manager = _MANAGER_CACHE.get(key)
        if manager is None:
            manager = LayerStackManager(key)
            _MANAGER_CACHE[key] = manager
        return manager


def clear_layer_stack_manager_cache() -> None:
    with _MANAGER_CACHE_LOCK:
        _MANAGER_CACHE.clear()


def drop_layer_stack_manager(layer_stack_root: str | Path) -> None:
    key = str(Path(layer_stack_root).resolve(strict=False))
    with _MANAGER_CACHE_LOCK:
        _MANAGER_CACHE.pop(key, None)


class LayerStackWorkspaceServer:
    """Owns binding and first base build for one layer-stack root."""

    def __init__(self, layer_stack_root: str | Path) -> None:
        self.layer_stack_root = Path(layer_stack_root)
        self._manager = get_layer_stack_manager(self.layer_stack_root)

    def build_workspace_base(
        self,
        *,
        workspace_root: str | Path,
        reset: bool = False,
        timings: dict[str, float] | None = None,
    ) -> WorkspaceBinding:
        if reset:
            drop_layer_stack_manager(self.layer_stack_root)
        binding = build_workspace_base(
            workspace_root=workspace_root,
            layer_stack_root=self.layer_stack_root,
            reset=reset,
            timings=timings,
        )
        self._manager = get_layer_stack_manager(self.layer_stack_root)
        return binding

    def ensure_workspace_base(
        self,
        *,
        workspace_root: str | Path,
    ) -> tuple[WorkspaceBinding, bool]:
        binding = read_workspace_binding(self.layer_stack_root)
        if binding is not None:
            manifest_file = manifest_path(self.layer_stack_root)
            if not manifest_file.exists():
                raise WorkspaceBindingError(
                    f"active manifest is missing for workspace binding: {manifest_file}"
                )
            active = read_manifest(manifest_file)
            if active.version <= 0:
                raise WorkspaceBindingError(
                    f"active manifest is empty for workspace binding: {manifest_file}"
                )
            if Path(binding.workspace_root) != Path(workspace_root):
                raise WorkspaceBindingError(
                    "workspace binding points at a different workspace: "
                    f"{binding.workspace_root} != {workspace_root}"
                )
            return binding, False
        return self.build_workspace_base(
            workspace_root=workspace_root,
        ), True

    def prepare_workspace_snapshot(
        self,
        *,
        owner_request_id: str,
        ttl_seconds: float | None = None,
    ) -> PrepareWorkspaceSnapshotResult:
        binding = self._require_bound_active_workspace()
        return self._manager.prepare_workspace_snapshot(
            owner_request_id,
            workspace_ref=binding.workspace_root,
            ttl_seconds=ttl_seconds,
        )

    def release_workspace_snapshot(self, *, lease_id: str) -> bool:
        return self._manager.release_lease(lease_id)

    def _require_bound_active_workspace(self) -> WorkspaceBinding:
        binding = require_workspace_binding(self.layer_stack_root)
        manifest_file = manifest_path(self.layer_stack_root)
        if not manifest_file.exists():
            raise WorkspaceBindingError(
                f"active manifest is missing for workspace binding: {manifest_file}"
            )
        active = read_manifest(manifest_file)
        if active.version <= 0:
            raise WorkspaceBindingError(
                f"active manifest is empty for workspace binding: {manifest_file}"
            )
        return binding


__all__ = [
    "LayerStackWorkspaceServer",
    "clear_layer_stack_manager_cache",
    "drop_layer_stack_manager",
    "get_layer_stack_manager",
]
