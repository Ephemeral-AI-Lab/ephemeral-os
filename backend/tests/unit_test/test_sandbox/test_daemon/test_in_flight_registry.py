"""Unit tests for request-keyed daemon in-flight lifecycle."""

from __future__ import annotations

import asyncio

import pytest

from sandbox.daemon.handler import cancel as cancel_handler
from sandbox.daemon.rpc.in_flight import InFlightRequestRegistry


pytestmark = pytest.mark.asyncio


async def test_cancel_cancels_registered_request() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=60, reaper_interval_s=60)
    registry.register(
        "req-1",
        task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
    )

    assert registry.cancel("req-1") is True
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_heartbeat_refreshes_and_count_by_agent() -> None:
    foreground_task = asyncio.create_task(asyncio.sleep(60))
    background_task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=60, reaper_interval_s=60)
    registry.register(
        "foreground-req",
        foreground_task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
        background=False,
    )
    registry.register(
        "background-req",
        background_task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )

    assert registry.count_by_agent("agent-a") == 1
    assert registry.heartbeat(["background-req"]) == 1
    assert registry.heartbeat(["missing"]) == 0

    foreground_task.cancel()
    background_task.cancel()
    await asyncio.gather(foreground_task, background_task, return_exceptions=True)


async def test_ttl_reaper_cancels_stale_request() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=0.1, reaper_interval_s=60)
    registry.register(
        "req-1",
        task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )
    registry._by_request["req-1"].last_seen -= 1.0  # noqa: SLF001

    registry.reap_stale()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert registry.metrics() == {"active_requests": 0, "ttl_reaped_total": 1}


async def test_ttl_reaper_ignores_foreground_request() -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=0.1, reaper_interval_s=60)
    registry.register(
        "req-1",
        task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
        background=False,
    )
    registry._by_request["req-1"].last_seen -= 1.0  # noqa: SLF001

    registry.reap_stale()

    assert not task.cancelled()
    assert registry.metrics() == {"active_requests": 1, "ttl_reaped_total": 0}
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_cancel_handler_targets_payload_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    task = asyncio.create_task(asyncio.sleep(60))
    registry = InFlightRequestRegistry(ttl_seconds=60, reaper_interval_s=60)
    registry.register(
        "target-req",
        task,  # type: ignore[arg-type]
        agent_id="agent-a",
        op="api.v1.shell",
    )
    monkeypatch.setattr(
        "sandbox.daemon.handler.cancel.get_in_flight_registry",
        lambda: registry,
    )

    response = await cancel_handler.cancel({"request_id": "target-req"})
    await asyncio.gather(task, return_exceptions=True)

    assert response["cancelled"] is True
    assert task.cancelled()
