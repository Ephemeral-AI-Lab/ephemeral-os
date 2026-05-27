"""Process-local EphemeralPipeline registry and stop helpers."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from sandbox.daemon.occ_runtime_services import get_occ_runtime_services
from sandbox.layer_stack.workspace_binding import (
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)
from sandbox.overlay.mount_syscalls import mount_syscalls_supported
from sandbox.overlay.kernel_mount import umount
from sandbox.overlay.writable_dirs import overlay_writable_root

if TYPE_CHECKING:
    from sandbox.ephemeral_workspace.pipeline import EphemeralPipeline

logger = logging.getLogger(__name__)

_MAX_PIPELINES = 256
_PIPELINES: OrderedDict[str, EphemeralPipeline] = OrderedDict()
_PIPELINE_LOCKS: dict[str, asyncio.Lock] = {}
_STALE_RUNTIME_OVERLAYS_REAPED = False


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
    lock = _PIPELINE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        _reap_stale_runtime_overlay_dirs_once()
        pipeline = _PIPELINES.get(key)
        if pipeline is None:
            from sandbox.ephemeral_workspace.pipeline import EphemeralPipeline

            backend = get_occ_runtime_services(key_root.as_posix())
            pipeline = EphemeralPipeline(
                occ_client=backend.occ_client,
                workspace_ref=key_root.as_posix(),
                layer_stack=backend.layer_stack,
                workspace_root=effective_workspace.as_posix(),
            )
            _PIPELINES[key] = pipeline
            if len(_PIPELINES) > _MAX_PIPELINES:
                _PIPELINES.popitem(last=False)
        else:
            _PIPELINES.move_to_end(key)
        if start and not pipeline.is_mounted and mount_syscalls_supported():
            await pipeline.start()
        return pipeline


async def stop_all_overlays() -> None:
    pipelines = list(_PIPELINES.values())
    _PIPELINES.clear()
    _PIPELINE_LOCKS.clear()
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
        for key, pipeline in list(_PIPELINES.items())
        if key.partition("\0")[0] == key_root.as_posix()
    ]
    for key, _pipeline in cache_entries:
        _PIPELINES.pop(key, None)
        _PIPELINE_LOCKS.pop(key, None)

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
    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        key = candidate.as_posix()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _reap_stale_runtime_overlay_dirs_once() -> None:
    """Remove per-call overlay scratch left behind by a previous daemon process."""
    global _STALE_RUNTIME_OVERLAYS_REAPED
    if _STALE_RUNTIME_OVERLAYS_REAPED:
        return
    _STALE_RUNTIME_OVERLAYS_REAPED = True

    root = overlay_writable_root() / "runtime" / "overlay"
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("failed to inspect stale overlay runtime dir", exc_info=True)
        return

    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning(
                "failed to remove stale overlay runtime path %s",
                child,
                exc_info=True,
            )


def clear_overlay_registry_for_tests() -> None:
    global _STALE_RUNTIME_OVERLAYS_REAPED
    _PIPELINES.clear()
    _PIPELINE_LOCKS.clear()
    _STALE_RUNTIME_OVERLAYS_REAPED = False


__all__ = [
    "clear_overlay_registry_for_tests",
    "get_sandbox_overlay",
    "stop_all_overlays",
    "stop_sandbox_overlay",
]
