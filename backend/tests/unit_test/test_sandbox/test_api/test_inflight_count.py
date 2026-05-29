"""Tests for the ``inflight_count`` engine wrapper over the daemon op."""

from __future__ import annotations

import pytest

from sandbox.api.daemon_invocations import inflight_count
from sandbox.api.transport import DAEMON_OP_INFLIGHT_COUNT


class _CannedTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: dict[str, object],
        *,
        timeout: int,
    ) -> dict[str, object]:
        del timeout
        self.calls.append((sandbox_id, op, dict(payload)))
        return self._response


@pytest.mark.asyncio
async def test_returns_count_and_calls_expected_op() -> None:
    transport = _CannedTransport({"success": True, "count": 3})
    assert await inflight_count("sbx-1", "agent-1", transport=transport) == 3
    sandbox_id, op, payload = transport.calls[0]
    assert sandbox_id == "sbx-1"
    assert op == DAEMON_OP_INFLIGHT_COUNT
    assert payload == {"agent_id": "agent-1"}


@pytest.mark.asyncio
async def test_defaults_missing_count_to_zero() -> None:
    transport = _CannedTransport({"success": False, "error": {"kind": "daemon_busy"}})
    assert await inflight_count("sbx-1", "agent-1", transport=transport) == 0
