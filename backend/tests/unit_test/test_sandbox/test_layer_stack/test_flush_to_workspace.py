"""Tests for collapsing layer-stack state back into the workspace base."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.layer_stack import DeleteLayerChange, LayerStack, WriteLayerChange
from sandbox.layer_stack.workspace_base import build_workspace_base


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.as_posix()


def test_flush_to_workspace_rebuilds_base_from_active_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old.txt").write_text("old\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    manager = LayerStack(stack)
    manager.publish_changes(
        [
            WriteLayerChange(
                path="new.txt",
                source_path=_source(tmp_path, "new.txt", b"new\n"),
            ),
            DeleteLayerChange(path="old.txt"),
        ]
    )

    timings: dict[str, float] = {}
    manifest = manager.flush_to_workspace(
        workspace_root=workspace,
        timings=timings,
    )

    assert manifest.version == 1
    assert manifest.depth == 1
    assert not (workspace / "old.txt").exists()
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "new\n"
    assert manager.read_text("new.txt") == ("new\n", True)
    assert "layer_stack.flush.total_s" in timings


def test_flush_to_workspace_rejects_active_snapshot_leases(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "value.txt").write_text("value\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    manager = LayerStack(stack)
    lease = manager.acquire_snapshot_lease("test")

    with pytest.raises(RuntimeError, match="active leases"):
        manager.flush_to_workspace(workspace_root=workspace)

    manager.release_lease(lease.lease_id)
