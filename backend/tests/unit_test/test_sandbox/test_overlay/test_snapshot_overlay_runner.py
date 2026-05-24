"""Snapshot-overlay legacy route deletion checks."""

from __future__ import annotations

from sandbox.daemon.rpc.dispatcher import OP_TABLE


def test_legacy_overlay_run_route_is_removed() -> None:
    assert "overlay.run" not in OP_TABLE
