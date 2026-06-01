"""Same-session upperdir writes overlap and remain private."""

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


@pytest.mark.skipif(
    not database_configured(),
    reason="database URL not configured",
)
@pytest.mark.skipif(
    not live_e2e_heavy_enabled(),
    reason="heavy live e2e disabled in runner.live_e2e.heavy_enabled",
)
@pytest.mark.timeout(300)
async def test_iws_parallel_conflicting_upperdir_writes(
    iws_clean_sandbox,
    iws_audit_jsonl,
) -> None:
    sandbox_id = str(iws_clean_sandbox["sandbox_id"])
    agent_id = "agent-parallel-writes"
    token = uuid.uuid4().hex[:12]
    base_dir = f"/testbed/iws-parallel-{token}"
    same_path = f"{base_dir}/same.txt"
    one_path = f"{base_dir}/one.txt"
    two_path = f"{base_dir}/two.txt"

    opened = await _iws_rpc.enter(
        sandbox_id,
        agent_id,
        layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT,
    )
    assert opened.get("success") is True, opened
    try:
        loop = asyncio.get_requestning_loop()
        started = loop.time()
        results = await asyncio.gather(
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"mkdir -p {base_dir}; sleep 0.45; printf 'winner-A\\n' > {same_path}",
            ),
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"mkdir -p {base_dir}; sleep 0.10; printf 'winner-B\\n' > {same_path}",
            ),
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"mkdir -p {base_dir}; sleep 0.20; printf 'one\\n' > {one_path}",
            ),
            _iws_rpc.shell(
                sandbox_id,
                agent_id,
                f"mkdir -p {base_dir}; sleep 0.30; printf 'two\\n' > {two_path}",
            ),
        )
        wall_s = loop.time() - started
        assert all(result.get("success") for result in results), results

        jsonl = await iws_audit_jsonl()
        tool_calls = _iws_invariants.events_of_type(
            jsonl,
            "sandbox_isolated_workspace_tool_call",
        )
        durations = [
            float((row.get("payload") or {}).get("duration_s") or 0.0)
            for row in tool_calls[-4:]
        ]
        assert len(durations) == 4, tool_calls
        assert max(durations) >= 0.40, durations
        assert wall_s < sum(durations) * 0.75, (
            "same-session writes should overlap instead of serializing "
            f"through a per-call session lock; wall={wall_s:.2f}s "
            f"durations={durations!r}",
        )

        same = await _iws_rpc.read_file(sandbox_id, agent_id, same_path)
        assert same.get("success") is True, same
        assert same.get("content") == "winner-A\n", (
            "same-file conflict should be deterministic last-finish-wins",
            same,
        )
        one = await _iws_rpc.read_file(sandbox_id, agent_id, one_path)
        two = await _iws_rpc.read_file(sandbox_id, agent_id, two_path)
        assert one.get("content") == "one\n", one
        assert two.get("content") == "two\n", two
    finally:
        await _iws_rpc.exit_(sandbox_id, agent_id)

    for path in (same_path, one_path, two_path):
        default_read = await _iws_rpc.read_file(sandbox_id, agent_id, path)
        assert default_read.get("success") is True, default_read
        assert default_read.get("exists") is False, default_read
