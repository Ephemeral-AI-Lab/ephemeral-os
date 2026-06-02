"""Daemon restart during parallel iws calls reaps old private state."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from sandbox.api import raw_exec
from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
)
from test_runner.tests.mock.sandbox.isolated_workspace._iws_fixtures import (
    daemon_kill_and_respawn,
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
@pytest.mark.timeout(420)
async def test_iws_daemon_restart_mid_parallel_calls(
    iws_clean_sandbox,
    iws_audit_jsonl,
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    agent_id = "agent-restart-mid-calls"
    token = uuid.uuid4().hex[:12]
    base_dir = f"/testbed/iws-restart-{token}"
    old_paths = tuple(f"{base_dir}/old-{index}.txt" for index in range(3))

    opened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert opened.get("success") is True, opened
    enter_jsonl = await iws_audit_jsonl()
    enter_events = _iws_invariants.events_of_type(
        enter_jsonl,
        "sandbox_isolated_workspace_enter",
        predicate=lambda row: (row.get("payload") or {}).get("agent_id")
        == agent_id,
    )
    assert enter_events, enter_jsonl.read_text(encoding="utf-8", errors="replace")
    old_handle_id = str((enter_events[-1].get("payload") or {}).get("workspace_handle_id") or "")
    assert old_handle_id, opened

    tasks = [
        asyncio.create_task(
            _iws_rpc.exec_command(
                sandbox_id,
                agent_id,
                f"mkdir -p {base_dir}; sleep 5; printf stale > {path}",
                timeout=20,
            )
        )
        for path in old_paths
    ]
    await asyncio.sleep(0.5)
    await raw_exec(
        sandbox_id,
        "pkill -9 -f '^/eos/daemon/eosd daemon' || true; "
        "pkill -9 -f '^.*python.*-m sandbox\\.daemon' || true; "
        "rm -f /eos/daemon/runtime.sock /eos/daemon/runtime.pid "
        "/eos/daemon/runtime.env",
        cwd="/",
        timeout=10,
    )

    done, pending = await asyncio.wait(tasks, timeout=30)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Every in-flight client should either fail transport/timeout or have
    # completed before the SIGKILL. A success after the kill is not itself a
    # correctness bug; leaked upperdir state after restart is.
    _ = [task.exception() if not task.cancelled() else None for task in done]

    await daemon_kill_and_respawn(
        sandbox_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )

    reopened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert reopened.get("success") is True, reopened
    try:
        jsonl = await iws_audit_jsonl()
        reopened_events = _iws_invariants.events_of_type(
            jsonl,
            "sandbox_isolated_workspace_enter",
            predicate=lambda row: (row.get("payload") or {}).get("agent_id")
            == agent_id,
        )
        assert reopened_events, jsonl.read_text(encoding="utf-8", errors="replace")
        new_handle_id = str(
            (reopened_events[-1].get("payload") or {}).get("workspace_handle_id") or ""
        )
        assert new_handle_id and new_handle_id != old_handle_id, reopened_events
        for path in old_paths:
            read = await _iws_rpc.read_file(sandbox_id, agent_id, path)
            assert read.get("success") is True, read
            assert read.get("exists") is False, (
                "old upperdir content leaked into fresh post-restart handle",
                path,
                read,
            )

        gc_events = _iws_invariants.events_of_type(
            jsonl,
            "sandbox_isolated_workspace_gc_orphan",
        )
        assert gc_events, "restart should emit orphan cleanup audit events"
        for row in gc_events:
            payload = row.get("payload") or {}
            assert payload.get("kind"), payload
            assert payload.get("identifier"), payload
            assert isinstance(payload.get("total_ms"), (int, float)), payload
            phases = _iws_invariants.phase_timing_extractor(payload)
            assert phases, payload
            _iws_invariants.assert_subset_cover(
                phases,
                float(payload.get("total_ms") or 0.0),
                label="gc_orphan",
            )
    finally:
        await _iws_rpc.exit_(sandbox_id, agent_id)
