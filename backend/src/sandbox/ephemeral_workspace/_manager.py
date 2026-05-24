"""Process-local EphemeralPipeline cache and stop helpers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any

from sandbox.daemon.occ_backend import build_occ_backend
from sandbox.layer_stack.workspace_binding import (
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)
from sandbox.overlay.capability import new_mount_api_supported
from sandbox.overlay.kernel_mount import umount

_MAX_OVERLAYS = 256
_OVERLAYS: OrderedDict[str, Any] = OrderedDict()
_LOCKS: dict[str, asyncio.Lock] = {}


async def get_sandbox_overlay(
    layer_stack_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    start: bool = True,
) -> Any:
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
            from sandbox.ephemeral_workspace.pipeline import EphemeralPipeline

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
    "_OVERLAYS",
    "clear_overlay_manager_for_tests",
    "get_sandbox_overlay",
    "stop_all_overlays",
    "stop_sandbox_overlay",
]
