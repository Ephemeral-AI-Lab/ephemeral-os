"""Daemon OP_TABLE routing invariants."""

from __future__ import annotations

import importlib

import pytest

from sandbox.overlay.handlers import run as overlay_run
from sandbox.daemon.handlers import (
    edit,
    health,
    metrics,
    read,
    shell,
    workspace,
    write,
)
from sandbox.daemon.rpc import dispatcher as server


def test_daemon_op_table_routes_to_current_handler_layout() -> None:
    server._load_peer_bootstraps()

    expected = {
        "api.write_file": write.write_file,
        "api.edit_file": edit.edit_file,
        "api.read_file": read.read_file,
        "api.shell": shell.shell,
        "api.layer_metrics": metrics.layer_metrics,
        "api.ensure_workspace_base": workspace.ensure_workspace_base,
        "api.build_workspace_base": workspace.build_workspace_base,
        "api.prepare_workspace_snapshot": (
            workspace.prepare_workspace_snapshot
        ),
        "api.release_workspace_snapshot": (
            workspace.release_workspace_snapshot
        ),
        "api.workspace_binding": workspace.workspace_binding,
        "overlay.run": overlay_run.handle,
        "api.runtime.ready": health.runtime_ready,
        "api.layer_stack.fence_stale_staging": (
            workspace.fence_stale_staging
        ),
    }
    assert server.OP_TABLE == expected


def test_daemon_op_table_does_not_route_through_occ_server() -> None:
    server._load_peer_bootstraps()

    for handler in server.OP_TABLE.values():
        assert handler.__module__ != "sandbox.daemon.services.occ_backend"
        assert "occ_handlers" not in handler.__module__


@pytest.mark.parametrize(
    "module_name",
    [
        "sandbox.daemon.occ_handlers",
        "sandbox.daemon.write_edit_handlers",
        "sandbox.daemon.api_handlers",
    ],
)
def test_legacy_daemon_modules_remain_deleted(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
