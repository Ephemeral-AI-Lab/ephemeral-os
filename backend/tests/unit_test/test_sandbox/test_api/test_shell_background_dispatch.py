"""Unit tests for shell background metadata on the single-RPC path."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from sandbox.api.tool.shell import shell as shell_api
from sandbox.api.transport import DAEMON_OP_SHELL
from sandbox._shared.models import SandboxCaller, ShellRequest


pytestmark = pytest.mark.asyncio


class _StubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: Mapping[str, object],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        self.calls.append((op, dict(payload)))
        return {
            "success": True,
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "changed_paths": ["modified.py"],
            "status": "ok",
            "timings": {},
            "warnings": [],
        }


def _request(*, background: bool) -> ShellRequest:
    return ShellRequest(
        invocation_id="shell-invocation-test",
        command="echo hi",
        cwd=".",
        timeout=60,
        background=background,
        caller=SandboxCaller(agent_id="test-agent"),
        description="shell.test",
    )


async def test_background_shell_uses_single_rpc_with_background_metadata() -> None:
    transport = _StubTransport()
    result = await shell_api(
        "sandbox-1",
        _request(background=True),
        transport=transport,
    )

    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.changed_paths == ("modified.py",)
    assert [op for op, _ in transport.calls] == [DAEMON_OP_SHELL]
    payload = transport.calls[0][1]
    assert payload["invocation_id"] == "shell-invocation-test"
    assert payload["background"] is True


async def test_foreground_shell_uses_same_single_rpc() -> None:
    transport = _StubTransport()
    result = await shell_api(
        "sandbox-1",
        _request(background=False),
        transport=transport,
    )

    assert result.success is True
    assert [op for op, _ in transport.calls] == [DAEMON_OP_SHELL]
    assert "background" not in transport.calls[0][1]
