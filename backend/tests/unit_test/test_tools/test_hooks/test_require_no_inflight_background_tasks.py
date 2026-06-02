"""Unit tests for ``RequireNoInflightBackgroundTasks``.

Covers the plan decision logic (G1, D5, D7): pass at zero; fail on a positive
local count (checked before the daemon); fail on a positive daemon count; the
daemon-error branch (fail-safe-block for success/root completion, fail-open for the
failure/blocker bail-out set); agent-id resolution; and the not-counted cases
(no sandbox, manager without ``count_by_agent``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

import sandbox.api as sandbox_api
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.runtime import ExecutionMetadata
from tools._hooks.require_no_inflight_background_tasks import (
    RequireNoInflightBackgroundTasks,
)


class _DummyInput(BaseModel):
    status: str | None = None
    deferred_goal_for_next_iteration: str | None = None


class _FakeManager:
    """Stand-in for ``BackgroundTaskSupervisor.count_by_agent``."""

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.seen: list[str] = []

    def count_by_agent(self, agent_id: str) -> int:
        self.seen.append(agent_id)
        return self._counts.get(agent_id, 0)


def _context(
    *,
    agent_run_id: str | None = "agent-1",
    agent_name: str = "",
    sandbox_id: str = "sbx-1",
    manager: object | None = None,
) -> ToolExecutionContextService:
    md = ExecutionMetadata(
        agent_run_id=agent_run_id,
        agent_name=agent_name,
        sandbox_id=sandbox_id,
    )
    if manager is not None:
        md.background_task_manager = manager
    return ToolExecutionContextService(cwd=Path("/tmp"), services=md)


def _hook(target: str = "enter_isolated_workspace") -> RequireNoInflightBackgroundTasks:
    return RequireNoInflightBackgroundTasks(target)


def _reason(result) -> str:
    return str(result.metadata.get("reason") or "")


def _patch_daemon(monkeypatch, *, count: int | None = None, error: bool = False) -> list[tuple]:
    calls: list[tuple] = []

    async def _fake(sandbox_id: str, agent_id: str) -> int:
        calls.append((sandbox_id, agent_id))
        if error:
            raise RuntimeError("daemon down")
        assert count is not None
        return count

    monkeypatch.setattr(sandbox_api, "command_session_count", _fake)
    return calls


# ----- pass at zero ---------------------------------------------------------
@pytest.mark.asyncio
async def test_passes_when_no_inflight(monkeypatch) -> None:
    calls = _patch_daemon(monkeypatch, count=0)
    ctx = _context(manager=_FakeManager({"agent-1": 0}))
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "pass"
    assert calls == [("sbx-1", "agent-1")]


# ----- local count short-circuits before the daemon -------------------------
@pytest.mark.asyncio
async def test_local_count_fails_without_calling_daemon(monkeypatch) -> None:
    calls = _patch_daemon(monkeypatch, error=True)  # would raise if called
    ctx = _context(manager=_FakeManager({"agent-1": 2}))
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "fail"
    assert _reason(result) == "ephemeral_jobs_in_flight"
    assert result.metadata.get("count") == 2
    assert calls == [], "daemon must not be consulted once local > 0"


# ----- daemon count fails ---------------------------------------------------
@pytest.mark.asyncio
async def test_daemon_count_fails(monkeypatch) -> None:
    _patch_daemon(monkeypatch, count=3)
    ctx = _context(manager=_FakeManager({"agent-1": 0}))
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "fail"
    assert _reason(result) == "ephemeral_jobs_in_flight"
    assert result.metadata.get("count") == 3


# ----- daemon-error branch: fail-safe-block vs fail-open (D7) ---------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "enter_isolated_workspace",
        "exit_isolated_workspace",
        "submit_root_outcome",
        "submit_generator_outcome",
        "submit_planner_outcome",
        "submit_reducer_outcome",
    ],
)
async def test_daemon_error_fail_safe_blocks_non_bailout(monkeypatch, target) -> None:
    _patch_daemon(monkeypatch, error=True)
    ctx = _context(manager=_FakeManager({"agent-1": 0}))
    result = await _hook(target).run(_DummyInput(), ctx)
    assert result.status == "fail"
    assert _reason(result) == "command_session_count_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "tool_input"),
    [
        ("submit_generator_outcome", _DummyInput(status="failed")),
        ("submit_reducer_outcome", _DummyInput(status="failed")),
        (
            "submit_planner_outcome",
            _DummyInput(deferred_goal_for_next_iteration="continue later"),
        ),
    ],
)
async def test_daemon_error_fail_open_for_bailout_terminals(
    monkeypatch, target, tool_input
) -> None:
    _patch_daemon(monkeypatch, error=True)
    ctx = _context(manager=_FakeManager({"agent-1": 0}))
    result = await _hook(target).run(tool_input, ctx)
    assert result.status == "pass"
    assert _reason(result) == "daemon_unavailable_bailout"


@pytest.mark.asyncio
async def test_daemon_error_with_local_inflight_still_blocks_bailout(monkeypatch) -> None:
    """Bail-out exemption is daemon-error-only; confirmed local in-flight wins."""
    _patch_daemon(monkeypatch, error=True)
    ctx = _context(manager=_FakeManager({"agent-1": 1}))
    result = await _hook("submit_generator_outcome").run(_DummyInput(status="failed"), ctx)
    assert result.status == "fail"
    assert _reason(result) == "ephemeral_jobs_in_flight"


# ----- agent-id resolution --------------------------------------------------
@pytest.mark.asyncio
async def test_agent_id_falls_back_to_agent_name(monkeypatch) -> None:
    calls = _patch_daemon(monkeypatch, count=0)
    manager = _FakeManager({"named-agent": 0})
    ctx = _context(agent_run_id="", agent_name="named-agent", manager=manager)
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "pass"
    assert manager.seen == ["named-agent"]
    assert calls == [("sbx-1", "named-agent")]


# ----- not-counted cases ----------------------------------------------------
@pytest.mark.asyncio
async def test_no_sandbox_id_passes_without_daemon(monkeypatch) -> None:
    calls = _patch_daemon(monkeypatch, error=True)  # would raise if called
    ctx = _context(sandbox_id="", manager=_FakeManager({"agent-1": 0}))
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "pass"
    assert calls == [], "no sandbox → no daemon call"


@pytest.mark.asyncio
async def test_missing_manager_treats_local_as_zero(monkeypatch) -> None:
    _patch_daemon(monkeypatch, count=0)
    ctx = _context(manager=None)  # no background_task_manager in context
    result = await _hook().run(_DummyInput(), ctx)
    assert result.status == "pass"
