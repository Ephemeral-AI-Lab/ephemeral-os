"""Unit tests for the new mode tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from task_center.graph import TaskGraph
from tools.core.base import ToolExecutionContextService
from tools.core.runtime import ExecutionMetadata
from tools.mode_tool.request_plan import (
    RequestPlanInput,
    request_plan,
)
from tools.mode_tool.submit_task_failure import (
    TaskFailureInput,
    submit_task_failure,
)
from tools.mode_tool.submit_task_success import (
    TaskSuccessInput,
    submit_task_success,
)


@dataclass
class _FakeTC:
    graph: TaskGraph = field(default_factory=TaskGraph)
    calls: list[tuple] = field(default_factory=list)

    def submit_task_success(self, task_id, summary):
        self.calls.append(("success", task_id, summary))

    def submit_task_failure(self, task_id, summary):
        self.calls.append(("task_failure", task_id, summary))

    def request_plan(self, task_id, request_plan_note):
        self.calls.append(("request_plan", task_id, request_plan_note))


def _ctx(tc, *, task_id="self", role="executor") -> ToolExecutionContextService:
    meta = ExecutionMetadata()
    meta["task_center"] = tc
    meta["task_id"] = task_id
    meta["role"] = role
    return ToolExecutionContextService(cwd=Path("/tmp"), services=meta)


# --- submit_task_success ---


@pytest.mark.asyncio
async def test_success_calls_task_center() -> None:
    tc = _FakeTC()
    arg = TaskSuccessInput(summary="ok")
    res = await submit_task_success.execute(arg, _ctx(tc, task_id="t1"))
    assert res.is_error is False
    assert json.loads(res.output)["status"] == "accepted"
    assert tc.calls == [("success", "t1", "ok")]


@pytest.mark.asyncio
async def test_success_missing_metadata() -> None:
    bad_ctx = ToolExecutionContextService(cwd=Path("/tmp"), services=ExecutionMetadata())
    res = await submit_task_success.execute(TaskSuccessInput(summary="x"), bad_ctx)
    assert res.is_error is True
    assert "executor-only" in res.output


@pytest.mark.asyncio
async def test_success_executor_only() -> None:
    tc = _FakeTC()
    res = await submit_task_success.execute(
        TaskSuccessInput(summary="ok"), _ctx(tc, task_id="t1", role="verifier")
    )
    assert res.is_error is True
    assert "executor-only" in res.output


# --- submit_task_failure ---


@pytest.mark.asyncio
async def test_task_failure_executor_only() -> None:
    tc = _FakeTC()
    res = await submit_task_failure.execute(
        TaskFailureInput(summary="boom"), _ctx(tc, task_id="t1", role="verifier")
    )
    assert res.is_error is True
    assert "executor-only" in res.output
    assert tc.calls == []


@pytest.mark.asyncio
async def test_task_failure_accepts_executor() -> None:
    tc = _FakeTC()
    res = await submit_task_failure.execute(
        TaskFailureInput(summary="boom"), _ctx(tc, task_id="t1", role="executor")
    )
    assert res.is_error is False
    assert tc.calls == [("task_failure", "t1", "boom")]


# --- request_plan ---


@pytest.mark.asyncio
async def test_request_plan_accepts_executor() -> None:
    tc = _FakeTC()
    res = await request_plan.execute(
        RequestPlanInput(request_plan_note="please plan"),
        _ctx(tc, task_id="x", role="executor"),
    )
    assert res.is_error is False
    assert tc.calls == [("request_plan", "x", "please plan")]


@pytest.mark.asyncio
async def test_request_plan_executor_only() -> None:
    tc = _FakeTC()
    res = await request_plan.execute(
        RequestPlanInput(request_plan_note="please plan"),
        _ctx(tc, task_id="x", role="verifier"),
    )
    assert res.is_error is True
    assert "executor-only" in res.output
    assert tc.calls == []
