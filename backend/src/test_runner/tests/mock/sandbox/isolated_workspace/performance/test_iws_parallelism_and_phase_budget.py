"""Plan-level parallelism and phase-budget proof for isolated workspaces."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from test_runner.tests._live_config import (
    database_configured,
    live_e2e_heavy_enabled,
)
from test_runner.tests.mock.sandbox.isolated_workspace import (
    _iws_invariants,
    _iws_rpc,
)


pytestmark = pytest.mark.asyncio
_AGENTS = (
    "agent-perf-A",
    "agent-perf-B",
    "agent-perf-C",
    "agent-perf-D",
    "agent-perf-E",
)


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(480)
async def test_iws_parallelism_and_phase_budget(
    iws_clean_sandbox,
    iws_audit_jsonl,
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    token = uuid.uuid4().hex[:12]
    agent_id = _AGENTS[0]
    base_dir = f"/testbed/iws-perf-{token}"
    opened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert opened.get("success") is True, opened
    loop = asyncio.get_requestning_loop()
    try:
        baseline_t0 = loop.time()
        baseline = await _iws_rpc.shell(sandbox_id, agent_id, "sleep 1")
        baseline_wall_s = loop.time() - baseline_t0
        assert baseline.get("success") is True, baseline

        parallel_t0 = loop.time()
        parallel = await asyncio.gather(
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"sleep 1; mkdir -p {base_dir}; touch {base_dir}/one.txt",
            ),
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"sleep 1; mkdir -p {base_dir}; touch {base_dir}/two.txt",
            ),
        )
        parallel_wall_s = loop.time() - parallel_t0
        assert all(result.get("success") for result in parallel), parallel
        assert parallel_wall_s <= baseline_wall_s * 1.5, (
            "same-session sleep calls should overlap within the plan budget; "
            f"baseline={baseline_wall_s:.2f}s parallel={parallel_wall_s:.2f}s",
        )
    finally:
        await _iws_rpc.exit_(sandbox_id, agent_id)

    enters = await asyncio.gather(
        *(
            _iws_rpc.enter(
                sandbox_id,
                agent,
                layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
            )
            for agent in _AGENTS
        )
    )
    try:
        assert all(result.get("success") for result in enters), enters
        root_hashes = {result.get("manifest_root_hash") for result in enters}
        assert len(root_hashes) == 1, root_hashes

        jsonl = await iws_audit_jsonl()
        for event_type in (
            "sandbox_isolated_workspace_enter",
            "sandbox_isolated_workspace_exit",
            "sandbox_isolated_workspace_tool_call",
            "sandbox_isolated_workspace_evicted",
            "sandbox_isolated_workspace_gc_orphan",
        ):
            for row in _iws_invariants.events_of_type(jsonl, event_type):
                payload = row.get("payload") or {}
                phases = _iws_invariants.phase_timing_extractor(payload)
                if phases:
                    _iws_invariants.assert_subset_cover(
                        phases,
                        payload.get("total_ms", 0.0),
                        label=event_type,
                    )

        enter_events = _iws_invariants.events_of_type(
            jsonl,
            "sandbox_isolated_workspace_enter",
        )
        perf_enters = [
            row for row in enter_events
            if (row.get("payload") or {}).get("agent_id") in _AGENTS
        ]
        assert len(perf_enters) >= len(_AGENTS), perf_enters
        install_veth_ms = [
            _iws_invariants.phase_timing_extractor(row.get("payload") or {})[
                "install_veth"
            ]
            for row in perf_enters[-len(_AGENTS):]
            if "install_veth"
            in _iws_invariants.phase_timing_extractor(row.get("payload") or {})
        ]
        assert len(install_veth_ms) == len(_AGENTS), install_veth_ms
        median = _iws_invariants.median(install_veth_ms)
        assert max(install_veth_ms) <= 5 * median, install_veth_ms
        for row in perf_enters[-len(_AGENTS):]:
            payload = row.get("payload") or {}
            assert payload.get("tree-copy") is False, payload
            assert int(payload.get("lowerdir_layer_count") or 0) >= 1, payload
    finally:
        for agent in _AGENTS:
            await _iws_rpc.exit_(sandbox_id, agent)

    for path in (f"{base_dir}/one.txt", f"{base_dir}/two.txt"):
        default_read = await _iws_rpc.read_file(sandbox_id, agent_id, path)
        assert default_read.get("success") is True, default_read
        assert default_read.get("exists") is False, default_read
