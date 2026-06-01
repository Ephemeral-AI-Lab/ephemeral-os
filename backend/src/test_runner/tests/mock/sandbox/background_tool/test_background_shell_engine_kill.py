"""Engine-abandon TTL coverage for invocation-keyed background shell calls."""

from __future__ import annotations

import asyncio

import pytest

from sandbox.daemon.rpc.in_flight import InFlightInvocationRegistry


pytestmark = pytest.mark.asyncio


@pytest.mark.timeout(15)
async def test_ttl_reaper_cancels_abandoned_background_invocation() -> None:
    """A silent engine leaves a daemon invocation that the TTL reaper cancels."""
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightInvocationRegistry(ttl_seconds=0.1, reaper_interval_s=60)
    registry.register(
        "bg-invocation",
        task,
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )
    registry._by_invocation["bg-invocation"].last_seen -= 1.0  # noqa: SLF001

    registry.reap_stale()
    assert registry.count_by_agent("agent-a") == 1

    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert registry.metrics()["ttl_reaped_total"] == 1
    registry.deregister("bg-invocation")
    assert registry.metrics()["active_invocations"] == 0
