"""Tests for ``sandbox.api.tool.read``."""

from __future__ import annotations

import pytest

from sandbox.api import ReadFileRequest, SandboxCaller
import sandbox.api.tool.read as read_module


@pytest.mark.asyncio
async def test_read_file_dispatches_to_sandbox_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_daemon_api(sandbox_id, op, args, *, timeout):
        calls.append((sandbox_id, op, args, timeout))
        return {
            "success": True,
            "exists": True,
            "content": "hello",
            "encoding": "utf-8",
            "timings": {"api.read.total_s": 0.1},
        }

    monkeypatch.setattr(read_module, "call_daemon_api", fake_call_daemon_api)

    result = await read_module.read_file(
        "sb-1",
        ReadFileRequest(path="/workspace/a.txt", caller=SandboxCaller(agent_id="a")),
    )

    assert result.success is True
    assert result.exists is True
    assert result.content == "hello"
    assert not hasattr(result, "conflict")
    assert calls == [
        ("sb-1", "api.read_file", {"path": "/workspace/a.txt"}, 60),
    ]


@pytest.mark.asyncio
async def test_read_file_missing_file_maps_to_exists_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_daemon_api(sandbox_id, op, args, *, timeout):
        del sandbox_id, op, args, timeout
        return {
            "success": True,
            "exists": False,
            "content": "",
            "encoding": "utf-8",
            "timings": {},
        }

    monkeypatch.setattr(read_module, "call_daemon_api", fake_call_daemon_api)

    result = await read_module.read_file(
        "sb-1",
        ReadFileRequest(path="/missing", caller=SandboxCaller(agent_id="a")),
    )

    assert result.success is True
    assert result.exists is False
    assert result.content == ""
