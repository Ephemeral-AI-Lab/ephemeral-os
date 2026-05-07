"""Runtime OP_TABLE routing invariants."""

from __future__ import annotations

import importlib

import pytest

from sandbox.overlay.handlers import run as overlay_run
from sandbox.runtime import health_handlers, layer_stack_handlers, server
from sandbox.runtime.handlers import (
    edit_handler,
    metrics_handler,
    read_handler,
    shell_handler,
    write_handler,
)


def test_runtime_op_table_routes_to_current_handler_layout() -> None:
    server._load_peer_bootstraps()

    expected = {
        "api.write_file": write_handler.write_file,
        "api.edit_file": edit_handler.edit_file,
        "api.read_file": read_handler.read_file,
        "api.shell": shell_handler.shell,
        "api.layer_metrics": metrics_handler.layer_metrics,
        "api.ensure_workspace_base": layer_stack_handlers.ensure_workspace_base,
        "api.build_workspace_base": layer_stack_handlers.build_workspace_base,
        "api.prepare_workspace_snapshot": (
            layer_stack_handlers.prepare_workspace_snapshot
        ),
        "api.release_workspace_snapshot": (
            layer_stack_handlers.release_workspace_snapshot
        ),
        "api.workspace_binding": layer_stack_handlers.workspace_binding,
        "overlay.run": overlay_run.handle,
        "api.runtime.ready": health_handlers.runtime_ready,
        "api.layer_stack.fence_stale_staging": (
            layer_stack_handlers.fence_stale_staging
        ),
    }
    assert server.OP_TABLE == expected


def test_runtime_op_table_does_not_route_through_occ_server() -> None:
    server._load_peer_bootstraps()

    for handler in server.OP_TABLE.values():
        assert handler.__module__ != "sandbox.runtime.occ_server"
        assert "occ_handlers" not in handler.__module__


@pytest.mark.parametrize(
    "module_name",
    [
        "sandbox.runtime.occ_handlers",
        "sandbox.runtime.write_edit_handlers",
        "sandbox.runtime.api_handlers",
    ],
)
def test_legacy_runtime_modules_remain_deleted(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
