"""Process-local SandboxOverlay cache keyed by layer stack and workspace."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path

from sandbox.daemon.occ_backend import build_occ_backend
from sandbox.daemon.service.sandbox_overlay import SandboxOverlay
from sandbox.execution.overlay.capability import new_mount_api_supported
from sandbox.execution.overlay.kernel_mount import umount
from sandbox.layer_stack.workspace_binding import (
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)

_MAX_OVERLAYS = 256
_OVERLAYS: OrderedDict[str, SandboxOverlay] = OrderedDict()
_LOCKS: dict[str, asyncio.Lock] = {}


async def get_sandbox_overlay(
    layer_stack_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    start: bool = True,
) -> SandboxOverlay:
    """Return the daemon-owned overlay for a bound workspace."""
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
        overlay = _OVERLAYS.get(key)
        if overlay is None:
            backend = build_occ_backend(key_root.as_posix())
            overlay = SandboxOverlay(
                occ_client=backend.occ_client,
                workspace_ref=key_root.as_posix(),
                layer_stack=backend.layer_stack,
                workspace_root=effective_workspace.as_posix(),
            )
            _OVERLAYS[key] = overlay
            if len(_OVERLAYS) > _MAX_OVERLAYS:
                _OVERLAYS.popitem(last=False)
        else:
            _OVERLAYS.move_to_end(key)
        if start and not overlay.is_mounted and new_mount_api_supported():
            await overlay.start()
        return overlay


async def stop_all_overlays() -> None:
    overlays = list(_OVERLAYS.values())
    _OVERLAYS.clear()
    _LOCKS.clear()
    for overlay in overlays:
        await overlay.stop()


async def stop_sandbox_overlay(
    layer_stack_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, object]:
    """Detach stale public overlays for a layer-stack workspace.

    Workspace-base resets may intentionally rebind the layer stack from one
    public root to another, for example from ``/testbed`` to ``/ephemeral-os``.
    Teardown must therefore not require the requested root to match the current
    binding; it should peel both the requested mount and the currently bound
    mount before raw checkout commands run.
    """
    key_root = Path(layer_stack_root).resolve(strict=False)
    workspace_candidates = _workspace_unmount_candidates(key_root, workspace_root)
    cache_entries = [
        (key, overlay)
        for key, overlay in list(_OVERLAYS.items())
        if _cache_key_root(key) == key_root.as_posix()
    ]
    for key, _overlay in cache_entries:
        _OVERLAYS.pop(key, None)
        _LOCKS.pop(key, None)

    warnings: list[str] = []
    stopped = 0
    for _key, overlay in cache_entries:
        try:
            await overlay.stop()
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
    "clear_overlay_manager_for_tests",
    "get_sandbox_overlay",
    "stop_all_overlays",
    "stop_sandbox_overlay",
]
