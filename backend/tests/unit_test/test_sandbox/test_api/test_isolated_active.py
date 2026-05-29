"""Tests for the ``isolated_active`` engine wrapper over the existing
``api.isolated_workspace.status`` daemon op.

True iff the status payload reports ``open: True``; False on an
open-false payload and on the no-bootstrapped-pipeline error payload (which
has no ``open`` key). No daemon-side change — only the wrapper's mapping.
"""

from __future__ import annotations

import pytest

from sandbox.api.daemon_invocations import isolated_active
from sandbox.api.transport import DAEMON_OP_ISOLATED_WORKSPACE_STATUS


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


def test_op_constant_value() -> None:
    assert DAEMON_OP_ISOLATED_WORKSPACE_STATUS == "api.isolated_workspace.status"


@pytest.mark.asyncio
async def test_returns_true_when_open() -> None:
    transport = _CannedTransport({"success": True, "open": True})
    assert await isolated_active("sbx-1", "agent-1", transport=transport) is True
    sandbox_id, op, payload = transport.calls[0]
    assert sandbox_id == "sbx-1"
    assert op == DAEMON_OP_ISOLATED_WORKSPACE_STATUS
    assert payload == {"agent_id": "agent-1"}


@pytest.mark.asyncio
async def test_returns_false_when_open_false() -> None:
    transport = _CannedTransport({"success": True, "open": False})
    assert await isolated_active("sbx-1", "agent-1", transport=transport) is False


@pytest.mark.asyncio
async def test_returns_false_on_no_pipeline_error_payload() -> None:
    # No bootstrapped pipeline → error payload, no "open" key → not isolated.
    transport = _CannedTransport(
        {"success": False, "error": {"kind": "isolated_workspace_not_bootstrapped"}}
    )
    assert await isolated_active("sbx-1", "agent-1", transport=transport) is False
