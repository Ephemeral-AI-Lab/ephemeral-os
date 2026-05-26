"""Commit active layer-stack state back into the bound workspace base."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from sandbox._shared.clock import monotonic_now, record_elapsed
from sandbox.layer_stack.lease import LeaseRegistry
from sandbox.layer_stack.manifest import Manifest, read_manifest
from sandbox.layer_stack.paths import remove_path
from sandbox.layer_stack.publisher import LayerPublisher
from sandbox.layer_stack.squash import LayerCheckpointSquasher
from sandbox.layer_stack.view import MergedView
from sandbox.layer_stack.workspace_base import build_workspace_base


@dataclass(frozen=True)
class WorkspaceCommitResult:
    manifest: Manifest
    view: MergedView
    publisher: LayerPublisher
    checkpoint_squasher: LayerCheckpointSquasher


def commit_to_workspace(
    *,
    storage_root: Path,
    workspace_root: str | Path,
    manifest_path: Path,
    view: MergedView,
    leases: LeaseRegistry,
    lock: threading.RLock,
    timings: dict[str, float] | None = None,
) -> WorkspaceCommitResult:
    """Collapse the active manifest into ``workspace_root`` and rebuild base.

    Projects the active manifest into a temp tree, atomically swaps the
    workspace contents to that projection, resets layer storage, and rebuilds
    a fresh base layer from the workspace bytes. Refuses to run while any
    snapshot lease is active.
    """
    total_start = monotonic_now()
    workspace = Path(workspace_root)
    if not workspace.is_dir():
        raise ValueError(f"workspace_root does not exist: {workspace}")
    with lock:
        if leases.active_count() > 0:
            raise RuntimeError("commit_to_workspace blocked by active leases")
        active = read_manifest(manifest_path)

    projection_parent = storage_root / "runtime" / "commit"
    projection_parent.mkdir(parents=True, exist_ok=True)
    projected = Path(tempfile.mkdtemp(prefix="projected-", dir=str(projection_parent)))
    try:
        project_start = monotonic_now()
        view.project(projected, active, share_inodes=False)
        record_elapsed(timings, "layer_stack.commit_to_workspace.project_s", project_start)

        replace_start = monotonic_now()
        _replace_directory_contents(workspace, projected)
        record_elapsed(
            timings,
            "layer_stack.commit_to_workspace.replace_workspace_s",
            replace_start,
        )

        reset_start = monotonic_now()
        with lock:
            _clear_storage_root_for_commit(storage_root)
            build_workspace_base(
                workspace_root=workspace,
                layer_stack_root=storage_root,
            )
            next_view = MergedView(storage_root)
            next_publisher = LayerPublisher(storage_root)
            next_checkpoint_squasher = LayerCheckpointSquasher(storage_root)
            new_manifest = read_manifest(manifest_path)
        record_elapsed(
            timings,
            "layer_stack.commit_to_workspace.rebuild_base_s",
            reset_start,
        )
        record_elapsed(timings, "layer_stack.commit_to_workspace.total_s", total_start)
        return WorkspaceCommitResult(
            manifest=new_manifest,
            view=next_view,
            publisher=next_publisher,
            checkpoint_squasher=next_checkpoint_squasher,
        )
    finally:
        shutil.rmtree(projected, ignore_errors=True)


def _replace_directory_contents(destination: Path, source: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        remove_path(child)
    for child in source.iterdir():
        os.replace(child, destination / child.name)


def _clear_storage_root_for_commit(storage_root: Path) -> None:
    storage_root.mkdir(parents=True, exist_ok=True)
    for child in storage_root.iterdir():
        if child.name == ".storage-writer.lock":
            continue
        remove_path(child)


__all__ = ["WorkspaceCommitResult", "commit_to_workspace"]
