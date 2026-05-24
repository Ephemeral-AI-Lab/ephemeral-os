"""Daemon OP_TABLE routing invariants."""

from __future__ import annotations

from sandbox.daemon.handler import (
    cancel,
    health,
    metrics,
    workspace,
)
from sandbox.ephemeral_workspace.plugin import handler as plugin_handler
from sandbox.daemon.handler import edit, glob, grep, read, shell, write
from sandbox.daemon.rpc import dispatcher as server
from sandbox.isolated_workspace import handlers as iws_handlers


def test_daemon_op_table_routes_to_current_handler_layout() -> None:
    server._load_peer_bootstraps()

    expected = {
        "api.write_file": write.write_file,
        "api.v1.write_file": write.write_file,
        "api.edit_file": edit.edit_file,
        "api.v1.edit_file": edit.edit_file,
        "api.read_file": read.read_file,
        "api.v1.read_file": read.read_file,
        "api.glob": glob.glob,
        "api.v1.glob": glob.glob,
        "api.grep": grep.grep,
        "api.v1.grep": grep.grep,
        "api.v1.shell": shell.shell,
        "api.v1.cancel": cancel.cancel,
        "api.v1.heartbeat": cancel.heartbeat,
        "api.v1.inflight_count": cancel.inflight_count,
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
        "api.runtime.ready": health.runtime_ready,
        "api.layer_stack.fence_stale_staging": (
            workspace.fence_stale_staging
        ),
        "api.plugin.ensure": plugin_handler.plugin_ensure,
        "api.plugin.status": plugin_handler.plugin_status,
        "api.isolated_workspace.enter": iws_handlers.enter,
        "api.isolated_workspace.exit": iws_handlers.exit_,
        "api.isolated_workspace.status": iws_handlers.status,
        "api.isolated_workspace.list_open": iws_handlers.list_open,
        "api.isolated_workspace.test_reset": iws_handlers.test_reset,
    }
    # Plugin-specific ops (plugin.<name>.<op>) appear when api.plugin.ensure
    # flushes pending registrations; only the static OP_TABLE entries are
    # asserted here.
    static_ops = {
        op: handler
        for op, handler in server.OP_TABLE.items()
        if not op.startswith("plugin.")
    }
    assert static_ops == expected


def test_daemon_op_table_does_not_route_through_occ_server() -> None:
    server._load_peer_bootstraps()

    for handler in server.OP_TABLE.values():
        assert handler.__module__ != "sandbox.daemon.occ_backend"
