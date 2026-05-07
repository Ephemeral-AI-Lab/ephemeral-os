"""Tests for ``sandbox.api.tool.shell``."""

from __future__ import annotations

import pytest

from sandbox.api import SandboxCaller, ShellRequest
from sandbox.api.tool.shell import shell


@pytest.mark.asyncio
async def test_shell_dispatches_to_sandbox_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_daemon_api(sandbox_id, op, args, *, timeout):
        calls.append((sandbox_id, op, args, timeout))
        return {
            "success": True,
            "exit_code": 0,
            "stdout": "new\n",
            "stderr": "",
            "changed_paths": ["pkg/value.txt"],
            "status": "ok",
            "conflict": None,
            "conflict_reason": None,
            "warnings": [],
            "timings": {"api.shell.total_s": 0.2},
        }

    monkeypatch.setattr(
        "sandbox.api.tool.shell.call_daemon_api",
        fake_call_daemon_api,
    )

    result = await shell(
        "sb-shell",
        ShellRequest(
            command="printf 'new\\n'",
            cwd=".",
            timeout=12,
            caller=SandboxCaller(agent_id="agent-1"),
            description="shell test",
        ),
    )

    assert result.success is True
    assert result.status == "ok"
    assert result.exit_code == 0
    assert result.stdout == "new\n"
    assert result.changed_paths == ("pkg/value.txt",)
    assert calls == [
        (
            "sb-shell",
            "api.shell",
            {
                "command": "printf 'new\\n'",
                "cwd": ".",
                "timeout_seconds": 12,
                "actor_id": "agent-1",
                "description": "shell test",
            },
            42,
        )
    ]


@pytest.mark.asyncio
async def test_shell_rejects_stdin_without_daemon_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_daemon_api(*_args, **_kwargs):
        raise AssertionError("daemon dispatch should not be called")

    monkeypatch.setattr(
        "sandbox.api.tool.shell.call_daemon_api",
        fail_call_daemon_api,
    )

    result = await shell(
        "sb-shell",
        ShellRequest(
            command="cat",
            stdin="input",
            caller=SandboxCaller(agent_id="agent-1"),
        ),
    )

    assert result.success is False
    assert result.status == "error"
    assert result.conflict is not None
    assert result.conflict.reason == "stdin_not_supported"
