"""Tests for ``sandbox.api.tool.write``."""

from __future__ import annotations

import pytest

from sandbox.api import SandboxCaller, WriteFileRequest
from sandbox.api.tool.write import write_file


@pytest.mark.asyncio
async def test_write_file_dispatches_to_sandbox_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_daemon_api(sandbox_id, op, args, *, timeout):
        calls.append((sandbox_id, op, args, timeout))
        return {
            "success": True,
            "changed_paths": ["a.py"],
            "status": "ok",
            "conflict": None,
            "conflict_reason": None,
            "timings": {"api.write.total_s": 0.1},
        }

    monkeypatch.setattr(
        "sandbox.api.tool.write.call_daemon_api",
        fake_call_daemon_api,
    )

    result = await write_file(
        "sb-write",
        WriteFileRequest(
            path="a.py",
            content="x",
            caller=SandboxCaller(agent_id="agent-1"),
            description="write a",
            overwrite=False,
        ),
    )

    assert result.success is True
    assert result.changed_paths == ("a.py",)
    assert result.timings["api.write.total_s"] == 0.1
    assert calls == [
        (
            "sb-write",
            "api.write_file",
            {
                "path": "a.py",
                "content": "x",
                "actor_id": "agent-1",
                "description": "write a",
                "overwrite": False,
            },
            60,
        )
    ]


@pytest.mark.asyncio
async def test_write_file_guard_failure_maps_conflict_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_daemon_api(sandbox_id, op, args, *, timeout):
        del sandbox_id, op, args, timeout
        return {
            "success": False,
            "changed_paths": [],
            "status": "aborted_version",
            "conflict": {
                "reason": "aborted_version",
                "conflict_file": "a.py",
                "message": "base_mismatch",
            },
            "conflict_reason": "base_mismatch",
            "timings": {},
        }

    monkeypatch.setattr(
        "sandbox.api.tool.write.call_daemon_api",
        fake_call_daemon_api,
    )

    result = await write_file(
        "sb-write-conflict",
        WriteFileRequest(
            path="a.py",
            content="x",
            caller=SandboxCaller(agent_id="agent-1"),
        ),
    )

    assert result.success is False
    assert result.status == "aborted_version"
    assert result.conflict is not None
    assert result.conflict.reason == "aborted_version"
    assert result.conflict.conflict_file == "a.py"
    assert result.conflict.message == "base_mismatch"
    assert result.conflict_reason == "base_mismatch"
