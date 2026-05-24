"""Engine-abandon TTL coverage for request-keyed background shell calls."""

from __future__ import annotations

import asyncio

import pytest

from sandbox.daemon.rpc.in_flight import InFlightRequestRegistry


pytestmark = pytest.mark.asyncio


@pytest.mark.timeout(15)
async def test_ttl_reaper_cancels_abandoned_background_request() -> None:
    """A silent engine leaves a daemon request that the TTL reaper cancels."""
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=0.1, reaper_interval_s=60)
    registry.register(
        "bg-request",
        task,
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )
    registry._by_request["bg-request"].last_seen -= 1.0  # noqa: SLF001

    registry.reap_stale()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert registry.metrics()["ttl_reaped_total"] == 1
