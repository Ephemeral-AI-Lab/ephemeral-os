"""Peer default publishes do not change an open iws lowerdir."""

from __future__ import annotations

import uuid

import pytest

from sandbox.host.daemon_client import call_daemon_api
from task_center_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
)
from task_center_runner.tests.mock.sandbox.isolated_workspace._iws_fixtures import (
    peer_publish_file,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(300)
async def test_iws_peer_publish_pin_and_refresh_boundary(
    iws_clean_sandbox,
    iws_audit_jsonl,
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    agent_id = "agent-peer-boundary"
    token = uuid.uuid4().hex[:12]
    path = f"/testbed/iws-peer-{token}.txt"

    await peer_publish_file(sandbox_id, path=path, body=f"version-A-{token}\n")
    opened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert opened.get("success") is True, opened
    pinned_version = opened.get("manifest_version")
    pinned_hash = opened.get("manifest_root_hash")
    assert pinned_version, opened
    assert pinned_hash, opened
    try:
        await peer_publish_file(sandbox_id, path=path, body=f"version-B-{token}\n")

        inside = await _iws_rpc.read_file(sandbox_id, agent_id, path)
        assert inside.get("success") is True, inside
        assert inside.get("content") == f"version-A-{token}\n", inside

        edited = await _iws_rpc.edit_file(
            sandbox_id,
            agent_id,
            path,
            [
                {
                    "old_text": f"version-A-{token}",
                    "new_text": f"private-IWS-{token}",
                    "expected_occurrences": 1,
                }
            ],
        )
        assert edited.get("success") is True, edited
        private = await _iws_rpc.read_file(sandbox_id, agent_id, path)
        assert private.get("content") == f"private-IWS-{token}\n", private

        status = await _iws_rpc.status(sandbox_id, agent_id)
        assert status.get("manifest_version") == pinned_version, status

        jsonl = await iws_audit_jsonl()
        enters = _iws_invariants.events_of_type(
            jsonl,
            "sandbox_isolated_workspace_enter",
            predicate=lambda row: (row.get("payload") or {}).get("agent_id")
            == agent_id,
        )
        assert enters, jsonl.read_text(encoding="utf-8", errors="replace")
        payload = enters[-1].get("payload") or {}
        assert payload.get("manifest_version") == pinned_version, payload
        assert payload.get("manifest_root_hash") == pinned_hash, payload
    finally:
        await _iws_rpc.exit_(sandbox_id, agent_id)

    default_read = await call_daemon_api(
        sandbox_id,
        "api.read_file",
        {"path": path},
        timeout=30,
    )
    assert default_read.get("success") is True, default_read
    assert default_read.get("content") == f"version-B-{token}\n", (
        "default mode must keep the peer publish and discard iws edits",
        default_read,
    )
