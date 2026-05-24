"""Phase 2 unified workspace dispatch invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.daemon import occ_backend, request_context
from sandbox.daemon.handler import edit, metrics, read, shell, write
from sandbox.daemon.rpc import dispatcher as server
from sandbox.daemon.workspace_server import get_layer_stack_manager
from sandbox.layer_stack.workspace_base import build_workspace_base


def test_request_context_classifier_helpers_removed() -> None:
    assert not hasattr(request_context, "ClassifiedPath")
    assert not hasattr(request_context, "classify_path")


def test_op_table_dispatches_data_ops_to_unified_handlers() -> None:
    server._load_peer_bootstraps()
    assert server.OP_TABLE["api.write_file"] is write.write_file
    assert server.OP_TABLE["api.v1.write_file"] is write.write_file
    assert server.OP_TABLE["api.edit_file"] is edit.edit_file
    assert server.OP_TABLE["api.v1.edit_file"] is edit.edit_file
    assert server.OP_TABLE["api.read_file"] is read.read_file
    assert server.OP_TABLE["api.v1.read_file"] is read.read_file
    assert server.OP_TABLE["api.v1.shell"] is shell.shell
    assert server.OP_TABLE["api.layer_metrics"] is metrics.layer_metrics


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_path", [["a", "b"], ("a", "b"), {"path": "a"}, 123, b"a"])
async def test_write_file_rejects_non_string_path_argument(bad_path: object) -> None:
    with pytest.raises(ValueError, match="single-path contract"):
        await write.write_file(
            {
                "layer_stack_root": "/tmp/unused-layer-stack",
                "path": bad_path,
                "content": "x",
            }
        )


@pytest.mark.asyncio
async def test_edit_file_rejects_list_path_argument() -> None:
    with pytest.raises(ValueError, match="single-path contract"):
        await edit.edit_file(
            {
                "layer_stack_root": "/tmp/unused-layer-stack",
                "path": ["a", "b"],
                "edits": [{"old_text": "x", "new_text": "y"}],
            }
        )


@pytest.mark.asyncio
async def test_read_file_rejects_list_path_argument() -> None:
    with pytest.raises(ValueError, match="single-path contract"):
        await read.read_file(
            {
                "layer_stack_root": "/tmp/unused-layer-stack",
                "path": ["a", "b"],
            }
        )


@pytest.mark.asyncio
async def test_layer_stack_services_share_lease_registry(tmp_path: Path) -> None:
    """Layer-stack services still share one manager/LeaseRegistry instance."""
    occ_backend.clear_backend_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("base\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)

    write_services = occ_backend.build_occ_backend(stack.as_posix())
    manager_via_singleton = get_layer_stack_manager(stack.as_posix())

    assert write_services.manager is manager_via_singleton
    assert write_services.layer_stack.manager is manager_via_singleton

    starting_active = manager_via_singleton.active_lease_count()
    lease = manager_via_singleton.acquire_snapshot_lease("test")
    try:
        assert manager_via_singleton.active_lease_count() == starting_active + 1
        assert (
            write_services.manager.active_lease_count()
            == manager_via_singleton.active_lease_count()
        )
    finally:
        manager_via_singleton.release_lease(lease.lease_id)


@pytest.mark.asyncio
async def test_layer_metrics_reports_no_cache_storage_fields(tmp_path: Path) -> None:
    occ_backend.clear_backend_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)

    payload = await metrics.layer_metrics({"layer_stack_root": stack.as_posix()})

    assert {
        "manifest_version",
        "manifest_depth",
        "active_leases",
        "pinned_layers",
        "layer_dirs",
        "staging_dirs",
        "storage_bytes",
        "workspace_bound",
        "workspace_root",
        "base_root_hash",
    } <= payload.keys()
    forbidden = {
        "cache_hit",
        "cache_policy",
        "lowerdir_cache_hits",
        "lowerdir_cache_misses",
        "lowerdir_cache_entries",
        "tree_copy_lowerdirs",
    }
    assert payload.keys().isdisjoint(forbidden)


@pytest.mark.asyncio
async def test_layer_metrics_reports_active_lease_pins(tmp_path: Path) -> None:
    occ_backend.clear_backend_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
    stack = tmp_path / "stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    manager = get_layer_stack_manager(stack.as_posix())
    lease = manager.acquire_snapshot_lease("metrics-reader")
    try:
        payload = await metrics.layer_metrics({"layer_stack_root": stack.as_posix()})
    finally:
        manager.release_lease(lease.lease_id)

    assert payload["active_leases"] == 1
    assert payload["pinned_layers"] == len(set(lease.manifest.layers))
