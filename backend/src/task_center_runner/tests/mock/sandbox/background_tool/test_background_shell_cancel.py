"""Phase 2.5 background cancellation lifecycle checks."""

from __future__ import annotations

import asyncio

import pytest

from sandbox.daemon import operation_handlers as cancel_handler
from sandbox.daemon.rpc.in_flight import InFlightInvocationRegistry


pytestmark = pytest.mark.asyncio


async def test_background_invocation_cancel_waits_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_ran = False

    async def _target() -> None:
        nonlocal cleanup_ran
        try:
            await asyncio.sleep(60)
        finally:
            cleanup_ran = True

    task = asyncio.create_task(_target())
    registry = InFlightInvocationRegistry(ttl_seconds=60, reaper_interval_s=60)
    registry.register(
        "invocation-1",
        task,
        agent_id="agent-a",
        op="api.v1.shell",
        background=True,
    )
    monkeypatch.setattr(
        "sandbox.daemon.operation_handlers.get_in_flight_registry",
        lambda: registry,
    )

    await asyncio.sleep(0)
    response = await cancel_handler.cancel({"invocation_id": "invocation-1"})

    assert response["cancelled"] is True
    assert response["cleanup_done"] is True
    assert cleanup_ran is True
    assert task.cancelled()
