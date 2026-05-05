"""Tests for ``sandbox.api.tool.shell``."""

from __future__ import annotations

import pytest

from sandbox.api import SandboxCaller, ShellRequest
from sandbox.api.tool.shell import shell, shell_batch


@pytest.mark.asyncio
async def test_shell_dispatches_to_sandbox_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_runtime_api(sandbox_id, op, args, *, timeout):
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
        "sandbox.api.tool.shell.call_runtime_api",
        fake_call_runtime_api,
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
async def test_shell_rejects_stdin_without_runtime_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_runtime_api(*_args, **_kwargs):
        raise AssertionError("runtime dispatch should not be called")

    monkeypatch.setattr(
        "sandbox.api.tool.shell.call_runtime_api",
        fail_call_runtime_api,
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


@pytest.mark.asyncio
async def test_shell_batch_dispatches_one_runtime_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_runtime_api(sandbox_id, op, args, *, timeout):
        calls.append((sandbox_id, op, args, timeout))
        return {
            "success": True,
            "results": [
                {
                    "success": True,
                    "exit_code": 0,
                    "stdout": "one\n",
                    "stderr": "",
                    "changed_paths": ["one.txt"],
                    "status": "ok",
                    "warnings": [],
                    "timings": {"api.shell.total_s": 0.1},
                },
                {
                    "success": True,
                    "exit_code": 0,
                    "stdout": "two\n",
                    "stderr": "",
                    "changed_paths": ["two.txt"],
                    "status": "ok",
                    "warnings": [],
                    "timings": {"api.shell.total_s": 0.2},
                },
            ],
            "warnings": [],
            "timings": {
                "api.shell_batch.total_s": 0.25,
                "api.shell_batch.count": 2.0,
                "api.shell_batch.max_concurrency": 2.0,
            },
        }

    monkeypatch.setattr(
        "sandbox.api.tool.shell.call_runtime_api",
        fake_call_runtime_api,
    )

    caller = SandboxCaller(agent_id="agent-1")
    results = await shell_batch(
        "sb-shell",
        (
            ShellRequest(
                command="printf one",
                cwd=".",
                timeout=10,
                caller=caller,
                description="first",
            ),
            ShellRequest(
                command="printf two",
                cwd="/ignored",
                timeout=20,
                caller=caller,
                description="second",
            ),
        ),
        max_concurrency=2,
    )

    assert [result.stdout for result in results] == ["one\n", "two\n"]
    assert results[0].changed_paths == ("one.txt",)
    assert results[1].changed_paths == ("two.txt",)
    assert results[0].timings["api.shell_batch.total_s"] == 0.25
    assert calls == [
        (
            "sb-shell",
            "api.shell_batch",
            {
                "max_concurrency": 2,
                "items": [
                    {
                        "command": "printf one",
                        "cwd": ".",
                        "timeout_seconds": 10,
                        "actor_id": "agent-1",
                        "description": "first",
                    },
                    {
                        "command": "printf two",
                        "cwd": ".",
                        "timeout_seconds": 20,
                        "actor_id": "agent-1",
                        "description": "second",
                    },
                ],
            },
            80,
        )
    ]
