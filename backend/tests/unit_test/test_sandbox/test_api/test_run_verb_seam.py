"""Regression test guarding the Scenario E mock-seam in _run_verb.

W7a consolidated read/write/edit verbs onto a shared _run_verb dispatcher. This
test asserts the seam still calls transport.call exactly once per verb, so that
existing recording-transport tests (test_read/test_write/test_edit) remain
trustworthy and the seam can't silently flip to bypassing the injected
transport.
"""

from __future__ import annotations

import pytest

from sandbox.api._impl._run_verb import _VerbSpec, _run_verb
from sandbox.models import ReadFileResult, SandboxCaller


class _SentinelTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object], int]] = []

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: dict[str, object],
        *,
        timeout: int,
    ) -> dict[str, object]:
        self.calls.append((sandbox_id, op, dict(payload), timeout))
        return {"ok": True}


class _StubRequest:
    def __init__(self) -> None:
        self.path = "/tmp/sentinel"
        self.caller = SandboxCaller(agent_id="stub-agent")


def _decode_result(_: dict[str, object]) -> ReadFileResult:
    return ReadFileResult(content="sentinel")


@pytest.mark.asyncio
async def test_run_verb_invokes_transport_call_exactly_once() -> None:
    sentinel = _SentinelTransport()
    spec = _VerbSpec(
        operation="read_file",
        daemon_op="api.read_file",
        timeout_s=10,
        payload_builder=lambda req: {"path": req.path},
        audit_payload_builder=lambda req: {"path": req.path},
        result_decoder=_decode_result,
    )

    await _run_verb(
        spec,
        "sandbox-x",
        _StubRequest(),
        audit_sink=None,
        transport=sentinel,
    )

    assert len(sentinel.calls) == 1
    sandbox_id, op, payload, timeout = sentinel.calls[0]
    assert sandbox_id == "sandbox-x"
    assert op == "api.read_file"
    assert payload == {"path": "/tmp/sentinel"}
    assert timeout == 10
