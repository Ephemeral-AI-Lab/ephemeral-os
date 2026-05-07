"""Runtime-local handlers for shared metric / service-cache helpers.

After the post-Phase-05 handler-per-command refactor, the host-facing
``api.{shell,write_file,edit_file,read_file}`` ops dispatch from
:mod:`sandbox.runtime.handlers` (one module per verb). Worker scaffolding
for shell still lives on :mod:`sandbox.runtime.command_exec_server`.

What stays here:

* ``api.layer_metrics`` — non-mutation diagnostic that summarizes layer
  storage and lease counts.
* ``drop_services_cache`` / ``_services_cache_clear`` — entrypoints
  that cascade cache drops across all peer runtime modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sandbox.layer_stack import LayerStackManager
from sandbox.layer_stack.workspace import read_workspace_binding
from sandbox.runtime.layer_stack_server import get_layer_stack_manager


_SERVICE_CACHE: dict[str, LayerStackManager] = {}


def _services_cache_clear() -> None:
    """Drop every peer-runtime service cache. Test helper."""
    _SERVICE_CACHE.clear()
    from sandbox.runtime import command_exec_server
    from sandbox.runtime.handlers import _common

    command_exec_server._services_cache_clear()
    _common._services_cache_clear()


def drop_services_cache(layer_stack_root: str) -> None:
    """Drop cached runtime services for one layer-stack root."""
    root = str(layer_stack_root or "").strip()
    if not root:
        return
    _SERVICE_CACHE.pop(root, None)
    _SERVICE_CACHE.pop(str(Path(root).resolve(strict=False)), None)
    from sandbox.runtime import command_exec_server
    from sandbox.runtime.handlers import _common

    command_exec_server.drop_services_cache(root)
    _common.drop_services_cache(root)


async def layer_metrics(args: dict[str, object]) -> dict[str, object]:
    manager = _manager(args)
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


def _manager(args: Mapping[str, object]) -> LayerStackManager:
    layer_stack_root = str(args.get("layer_stack_root") or "").strip()
    if not layer_stack_root:
        raise ValueError("layer_stack_root is required")
    cached = _SERVICE_CACHE.get(layer_stack_root)
    if cached is not None:
        return cached
    manager = get_layer_stack_manager(layer_stack_root)
    _SERVICE_CACHE[layer_stack_root] = manager
    return manager


__all__ = [
    "drop_services_cache",
    "layer_metrics",
]
