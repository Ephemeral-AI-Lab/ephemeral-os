"""Daemon OP_TABLE routing invariants."""

from __future__ import annotations

from sandbox.daemon import operation_handlers
from sandbox.daemon.rpc import dispatcher as server
from sandbox.ephemeral_workspace.plugin import runtime_api as plugin_runtime_api


def test_daemon_op_table_routes_to_current_handler_layout() -> None:
    server._register_builtin_operations()

    expected = {
        "api.write_file": operation_handlers.write_file,
        "api.v1.write_file": operation_handlers.write_file,
        "api.edit_file": operation_handlers.edit_file,
        "api.v1.edit_file": operation_handlers.edit_file,
        "api.read_file": operation_handlers.read_file,
        "api.v1.read_file": operation_handlers.read_file,
        "api.glob": operation_handlers.glob,
        "api.v1.glob": operation_handlers.glob,
        "api.grep": operation_handlers.grep,
        "api.v1.grep": operation_handlers.grep,
        "api.v1.shell": operation_handlers.shell,
        "api.v1.cancel": operation_handlers.cancel,
        "api.v1.heartbeat": operation_handlers.heartbeat,
        "api.v1.inflight_count": operation_handlers.inflight_count,
        "api.layer_metrics": operation_handlers.layer_metrics,
        "api.ensure_workspace_base": operation_handlers.ensure_workspace_base,
        "api.build_workspace_base": operation_handlers.build_workspace_base,
        "api.prepare_workspace_snapshot": operation_handlers.prepare_workspace_snapshot,
        "api.release_lease": operation_handlers.release_lease,
        "api.workspace_binding": operation_handlers.workspace_binding,
        "api.runtime.ready": operation_handlers.runtime_ready,
        "api.layer_stack.fence_stale_staging": operation_handlers.fence_stale_staging,
        "api.plugin.ensure": plugin_runtime_api.plugin_ensure,
        "api.plugin.status": plugin_runtime_api.plugin_status,
        "api.isolated_workspace.enter": server._isolated_workspace_enter,
        "api.isolated_workspace.exit": server._isolated_workspace_exit,
        "api.isolated_workspace.status": server._isolated_workspace_status,
        "api.isolated_workspace.list_open": server._isolated_workspace_list_open,
        "api.isolated_workspace.test_reset": server._isolated_workspace_test_reset,
    }
    # Plugin-specific ops (plugin.<name>.<op>) appear when api.plugin.ensure
    # flushes pending registrations; only the static OP_TABLE entries are
    # asserted here.
    static_ops = {
        op: handler for op, handler in server.OP_TABLE.items() if not op.startswith("plugin.")
    }
    assert static_ops == expected


def test_daemon_op_table_does_not_route_through_occ_server() -> None:
    server._register_builtin_operations()

    for handler in server.OP_TABLE.values():
        assert handler.__module__ != "sandbox.daemon.occ_runtime_services"
