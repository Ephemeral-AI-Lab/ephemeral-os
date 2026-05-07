"""Runtime-local handlers for shared metric / service-cache helpers.

After the post-Phase-05 handler-per-command refactor, the host-facing
``api.{shell,write_file,edit_file,read_file}`` ops dispatch from
:mod:`sandbox.runtime.handlers` (one module per verb). Worker scaffolding
for shell still lives on :mod:`sandbox.runtime.command_exec_server`.

What stays here:

* ``api.layer_metrics`` — non-mutation diagnostic that summarizes layer
  storage and lease counts.
* ``drop_services_cache`` / ``_services_cache_clear`` — entrypoints
  preserved for backward-compat. The OCC backend cache is owned by
  :mod:`sandbox.runtime.occ_server` post Phase 05.5; both helpers now
  delegate there.
"""

from __future__ import annotations

from collections.abc import Mapping

from sandbox.layer_stack import LayerStackManager
from sandbox.layer_stack.workspace import read_workspace_binding
from sandbox.runtime import occ_server


def _services_cache_clear() -> None:
    """Drop the shared OCC backend cache. Test helper."""
    occ_server._backend_cache_clear()


def drop_services_cache(layer_stack_root: str) -> None:
    """Drop cached runtime services for one layer-stack root."""
    occ_server.drop_backend_cache(layer_stack_root)


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
    return occ_server.build_occ_backend(layer_stack_root).manager


__all__ = [
    "drop_services_cache",
    "layer_metrics",
]
