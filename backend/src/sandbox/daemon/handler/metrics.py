"""``api.layer_metrics`` diagnostic dispatch entry."""

from __future__ import annotations

from sandbox.layer_stack.workspace_binding import read_workspace_binding
from sandbox.daemon.handler.request_context import layer_stack_root as require_layer_stack_root
from sandbox.daemon.service import occ_backend


async def layer_metrics(args: dict[str, object]) -> dict[str, object]:
    """Summarize layer-stack storage and lease state for one runtime root."""
    root = require_layer_stack_root(args)
    manager = occ_backend.build_occ_backend(root).manager
    manifest = manager.read_active_manifest()
    binding = read_workspace_binding(root)
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
        "base_root_hash": binding.base_root_hash if binding is not None else "",
    }


__all__ = ["layer_metrics"]
