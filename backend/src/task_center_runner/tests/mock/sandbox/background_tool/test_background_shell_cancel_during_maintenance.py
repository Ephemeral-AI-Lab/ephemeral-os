"""Phase 2.5 foreground/background in-flight accounting checks."""

from __future__ import annotations

import asyncio

import pytest

from sandbox.daemon.rpc.in_flight import InFlightInvocationRegistry


pytestmark = pytest.mark.asyncio


async def test_inflight_count_ignores_foreground_maintenance_invocation() -> None:
    foreground = asyncio.create_task(asyncio.sleep(60))
    background = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightInvocationRegistry(ttl_seconds=60, reaper_interval_s=60)
    registry.register(
        "foreground-maintenance",
        foreground,
        agent_id="agent-a",
        op="api.v1.shell",
        background=False,
    )
    registry.register(
        "background-shell",
        background,
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )

    assert registry.count_by_agent("agent-a") == 1

    foreground.cancel()
    background.cancel()
    await asyncio.gather(foreground, background, return_exceptions=True)
