"""Flush active layer-stack state back into the bound workspace base."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from sandbox._shared.clock import monotonic_now
from sandbox.layer_stack.lease import LeaseRegistry
from sandbox.layer_stack.manifest import FileManifestStore, Manifest
from sandbox.layer_stack.paths import remove_path
from sandbox.layer_stack.publisher import LayerPublisher
from sandbox.layer_stack.squash import SquashService
from sandbox.layer_stack.view import MergedView
from sandbox.layer_stack.workspace_base import build_workspace_base


@dataclass(frozen=True)
class WorkspaceFlushResult:
    manifest: Manifest
    view: MergedView
    publisher: LayerPublisher
    squash: SquashService


def flush_to_workspace(
    *,
    storage_root: Path,
    workspace_root: str | Path,
    manifest_store: FileManifestStore,
    view: MergedView,
    leases: LeaseRegistry,
    lock: threading.RLock,
    timings: dict[str, float] | None = None,
) -> WorkspaceFlushResult:
    """Collapse the active merged view into ``workspace_root`` and rebuild base."""
    total_start = monotonic_now()
    workspace = Path(workspace_root)
    if not workspace.is_dir():
        raise ValueError(f"workspace_root does not exist: {workspace}")
    with lock:
        if leases.active_count() > 0:
            raise RuntimeError("flush_to_workspace blocked by active leases")
        active = manifest_store.read()

    materialize_parent = storage_root / "runtime" / "flush"
    materialize_parent.mkdir(parents=True, exist_ok=True)
    materialized = Path(tempfile.mkdtemp(prefix="merged-", dir=str(materialize_parent)))
    try:
        materialize_start = monotonic_now()
        view.materialize(materialized, active, share_inodes=False)
        if timings is not None:
            timings["layer_stack.flush.materialize_s"] = monotonic_now() - materialize_start

        replace_start = monotonic_now()
        _replace_directory_contents(workspace, materialized)
        if timings is not None:
            timings["layer_stack.flush.replace_workspace_s"] = monotonic_now() - replace_start

        reset_start = monotonic_now()
        with lock:
            _clear_storage_root_for_flush(storage_root)
            build_workspace_base(
                workspace_root=workspace,
                layer_stack_root=storage_root,
            )
            next_view = MergedView(storage_root)
            next_publisher = LayerPublisher(storage_root)
            next_squash = SquashService(storage_root)
            new_manifest = manifest_store.read()
        if timings is not None:
            timings["layer_stack.flush.rebuild_base_s"] = monotonic_now() - reset_start
            timings["layer_stack.flush.total_s"] = monotonic_now() - total_start
        return WorkspaceFlushResult(
            manifest=new_manifest,
            view=next_view,
            publisher=next_publisher,
            squash=next_squash,
        )
    finally:
        shutil.rmtree(materialized, ignore_errors=True)


def _replace_directory_contents(destination: Path, source: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        remove_path(child)
    for child in source.iterdir():
        os.replace(child, destination / child.name)


def _clear_storage_root_for_flush(storage_root: Path) -> None:
    storage_root.mkdir(parents=True, exist_ok=True)
    for child in storage_root.iterdir():
        if child.name == ".storage-writer.lock":
            continue
        remove_path(child)


__all__ = ["WorkspaceFlushResult", "flush_to_workspace"]
