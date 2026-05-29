"""Unit tests for ``BlockInIsolatedMode`` (plan G2).

Block when the daemon reports the agent isolated; pass when not isolated, when
there is no sandbox (cannot be isolated), and — fail-open — when the daemon
status query errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

import sandbox.api as sandbox_api
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._hooks.block_in_isolated_mode import BlockInIsolatedMode


class _DummyInput(BaseModel):
    pass


def _context(*, sandbox_id: str = "sbx-1", agent_run_id: str | None = "agent-1"):
    md = ExecutionMetadata(agent_run_id=agent_run_id, sandbox_id=sandbox_id)
    return ToolExecutionContextService(cwd=Path("/tmp"), services=md)


def _hook() -> BlockInIsolatedMode:
    return BlockInIsolatedMode("ask_advisor")


def _patch_status(monkeypatch, *, active: bool | None = None, error: bool = False) -> list[tuple]:
    calls: list[tuple] = []

    async def _fake(sandbox_id: str, agent_id: str) -> bool:
        calls.append((sandbox_id, agent_id))
        if error:
            raise RuntimeError("daemon down")
        assert active is not None
        return active

    monkeypatch.setattr(sandbox_api, "isolated_active", _fake)
    return calls


@pytest.mark.asyncio
async def test_blocks_when_isolated(monkeypatch) -> None:
    calls = _patch_status(monkeypatch, active=True)
    result = await _hook().run(_DummyInput(), _context())
    assert result.status == "fail"
    assert result.metadata.get("reason") == "isolated_workspace_open"
    assert "exit_isolated_workspace" in result.reason
    assert calls == [("sbx-1", "agent-1")]


@pytest.mark.asyncio
async def test_passes_when_not_isolated(monkeypatch) -> None:
    _patch_status(monkeypatch, active=False)
    result = await _hook().run(_DummyInput(), _context())
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_passes_without_sandbox_id(monkeypatch) -> None:
    calls = _patch_status(monkeypatch, error=True)  # would raise if called
    result = await _hook().run(_DummyInput(), _context(sandbox_id=""))
    assert result.status == "pass"
    assert calls == [], "no sandbox → cannot be isolated, no daemon call"


@pytest.mark.asyncio
async def test_fail_open_on_daemon_error(monkeypatch) -> None:
    _patch_status(monkeypatch, error=True)
    result = await _hook().run(_DummyInput(), _context())
    assert result.status == "pass"
