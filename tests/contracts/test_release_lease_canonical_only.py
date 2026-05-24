"""Phase 2.6 follow-up: release leases through the canonical RPC only."""

from __future__ import annotations

import inspect


def test_release_lease_is_the_only_registered_release_rpc() -> None:
    from sandbox.daemon import handlers
    from sandbox.daemon.rpc import dispatcher

    dispatcher._load_peer_bootstraps()

    assert dispatcher.OP_TABLE["api.release_lease"] is handlers.release_lease
    assert "api.release_workspace_snapshot" not in dispatcher.OP_TABLE
    assert not hasattr(handlers, "release_workspace_snapshot")


def test_release_lease_handler_is_async() -> None:
    from sandbox.daemon import handlers

    assert inspect.iscoroutinefunction(handlers.release_lease)
