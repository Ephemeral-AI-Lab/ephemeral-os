"""Unit tests for the new mode tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from task_center.task_graph import TaskGraph
from tools.core.base import ToolExecutionContextService
from tools.core.runtime import ExecutionMetadata
from tools.mode_tool.launch_plan_handoff import (
    LaunchPlanHandoffInput,
    launch_plan_handoff,
)
from tools.mode_tool.submit_evaluation_failure import (
    EvaluationFailureInput,
    submit_evaluation_failure,
)
from tools.mode_tool.submit_plan_handoff import (
    PlanHandoffInput,
    submit_plan_handoff,
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

    def submit_evaluation_failure(self, task_id, summary):
        self.calls.append(("eval_failure", task_id, summary))

    def launch_plan_handoff(self, task_id, task_detail):
        self.calls.append(("launch_plan", task_id, task_detail))

    def submit_plan_handoff(self, task_id, tasks, task_inputs, summary):
        from task_center.plan import compile_dag
        compile_dag(tasks, task_inputs)
        self.calls.append(("plan_handoff", task_id, tasks, task_inputs, summary))


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
    assert "missing" in res.output


# --- submit_task_failure ---


@pytest.mark.asyncio
async def test_task_failure_executor_only() -> None:
    tc = _FakeTC()
    res = await submit_task_failure.execute(
        TaskFailureInput(summary="boom"), _ctx(tc, task_id="t1", role="evaluator")
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


# --- submit_evaluation_failure ---


@pytest.mark.asyncio
async def test_evaluation_failure_evaluator_only() -> None:
    tc = _FakeTC()
    res = await submit_evaluation_failure.execute(
        EvaluationFailureInput(summary="nope"),
        _ctx(tc, task_id="ev", role="executor"),
    )
    assert res.is_error is True
    assert "evaluator-only" in res.output
    assert tc.calls == []


@pytest.mark.asyncio
async def test_evaluation_failure_accepts_evaluator() -> None:
    tc = _FakeTC()
    res = await submit_evaluation_failure.execute(
        EvaluationFailureInput(summary="nope"),
        _ctx(tc, task_id="ev", role="evaluator"),
    )
    assert res.is_error is False
    assert tc.calls == [("eval_failure", "ev", "nope")]


# --- launch_plan_handoff ---


@pytest.mark.asyncio
async def test_launch_plan_handoff_executor_or_evaluator() -> None:
    tc = _FakeTC()
    res = await launch_plan_handoff.execute(
        LaunchPlanHandoffInput(task_detail="please plan"),
        _ctx(tc, task_id="x", role="executor"),
    )
    assert res.is_error is False
    assert tc.calls == [("launch_plan", "x", "please plan")]


# --- submit_plan_handoff ---


@pytest.mark.asyncio
async def test_plan_handoff_planner_only() -> None:
    tc = _FakeTC()
    arg = PlanHandoffInput(
        tasks=[{"id": "A"}],
        task_inputs={"A": "do A"},
        handoff_summary="root",
    )
    res = await submit_plan_handoff.execute(arg, _ctx(tc, task_id="p", role="executor"))
    assert res.is_error is True
    assert "planner-only" in res.output
    assert tc.calls == []


@pytest.mark.asyncio
async def test_plan_handoff_accepts_planner() -> None:
    tc = _FakeTC()
    arg = PlanHandoffInput(
        tasks=[{"id": "A"}, {"id": "B", "deps": ["A"]}],
        task_inputs={"A": "do A", "B": "do B"},
        handoff_summary="A then B",
    )
    res = await submit_plan_handoff.execute(arg, _ctx(tc, task_id="p", role="planner"))
    assert res.is_error is False
    assert tc.calls[0][0] == "plan_handoff"
    assert tc.calls[0][-1] == "A then B"


@pytest.mark.asyncio
async def test_plan_handoff_rejects_cycle() -> None:
    tc = _FakeTC()
    arg = PlanHandoffInput(
        tasks=[{"id": "A", "deps": ["B"]}, {"id": "B", "deps": ["A"]}],
        task_inputs={"A": "do A", "B": "do B"},
        handoff_summary="cycle",
    )
    res = await submit_plan_handoff.execute(arg, _ctx(tc, task_id="p", role="planner"))
    assert res.is_error is True
    assert "rejected" in res.output
    assert tc.calls == []
