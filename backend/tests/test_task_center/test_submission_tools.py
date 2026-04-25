"""Unit tests for the six submission/accessor tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from task_center import Status, Task
from task_center.graph import TaskGraph
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.runtime import ExecutionMetadata
from tools.submission.read_task_details import (
    ReadTaskDetailsInput,
    read_task_details,
)
from tools.submission.read_task_graph import (
    ReadTaskGraphInput,
    read_task_graph,
)
from tools.submission.submit_continue_to_work import (
    ContinueToWorkInput,
    submit_continue_to_work,
)
from tools.submission.submit_full_plan_handoff import (
    FullPlanHandoffInput,
    submit_full_plan_handoff,
)
from tools.submission.submit_partial_plan_handoff import (
    PartialPlanHandoffInput,
    submit_partial_plan_handoff,
)
from tools.submission.submit_task_completion import (
    TaskCompletionInput,
    submit_task_completion,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeTC:
    """Records submission calls; mirrors compile_phases for handoff inputs."""

    graph: TaskGraph = field(default_factory=TaskGraph)
    calls: list[tuple] = field(default_factory=list)

    def submit_task_completion(self, task_id, summary):
        self.calls.append(("complete", task_id, summary))

    def submit_full_handoff(self, task_id, phases, task_specs, ac):
        from task_center.phases import compile_phases
        compile_phases(phases, task_specs)  # raises PhaseValidationError on bad input
        self.calls.append(("full", task_id, phases, task_specs, ac))

    def submit_partial_handoff(self, task_id, phases, task_specs, ac, note):
        from task_center.phases import compile_phases
        compile_phases(phases, task_specs)
        self.calls.append(("partial", task_id, phases, task_specs, ac, note))

    def submit_continue_to_work(self, evaluator_id, summary):
        self.calls.append(("continue", evaluator_id, summary))


def _ctx(tc: _FakeTC, *, task_id: str = "self", role: str = "executor") -> ToolExecutionContext:
    meta = ExecutionMetadata()
    meta["task_center"] = tc
    meta["task_id"] = task_id
    meta["role"] = role
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=meta)


# --------------------------------------------------------------------------- #
# submit_task_completion                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_completion_calls_task_center() -> None:
    tc = _FakeTC()
    arg = TaskCompletionInput(summary="all good")
    res = await submit_task_completion.execute(arg, _ctx(tc, task_id="t1"))
    assert isinstance(res, ToolResult)
    assert res.is_error is False
    assert tc.calls == [("complete", "t1", "all good")]


@pytest.mark.asyncio
async def test_completion_missing_metadata() -> None:
    bad_ctx = ToolExecutionContext(cwd=Path("/tmp"), metadata=ExecutionMetadata())
    res = await submit_task_completion.execute(
        TaskCompletionInput(summary="x"), bad_ctx
    )
    assert res.is_error is True
    assert "missing" in res.output


# --------------------------------------------------------------------------- #
# submit_full_plan_handoff                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_full_handoff_happy_path() -> None:
    tc = _FakeTC()
    arg = FullPlanHandoffInput(
        phases=[[{"id": "A"}, {"id": "B"}]],
        task_specs={"A": {"title": "A", "spec": "..."}, "B": {"title": "B", "spec": "..."}},
        acceptance_criteria="Both A and B complete.",
    )
    res = await submit_full_plan_handoff.execute(arg, _ctx(tc, task_id="parent"))
    assert res.is_error is False
    assert tc.calls[0][0] == "full"
    assert tc.calls[0][1] == "parent"


@pytest.mark.asyncio
async def test_full_handoff_rejects_invalid_phases() -> None:
    """D1: invalid phase plan -> PhaseValidationError -> tool returns is_error."""
    tc = _FakeTC()
    arg = FullPlanHandoffInput(
        phases=[[{"id": "A", "needs": ["B"]}]],  # phase 1 with needs is illegal
        task_specs={"A": {"title": "A", "spec": "..."}, "B": {"title": "B", "spec": "..."}},
        acceptance_criteria="x",
    )
    res = await submit_full_plan_handoff.execute(arg, _ctx(tc, task_id="parent"))
    assert res.is_error is True
    assert "rejected" in res.output
    assert tc.calls == []  # no successful call recorded


# --------------------------------------------------------------------------- #
# submit_partial_plan_handoff                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_partial_handoff_requires_non_empty_note() -> None:
    """D2: handoff_note has min_length=1 — pydantic rejects empty string."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PartialPlanHandoffInput(
            phases=[[{"id": "A"}]],
            task_specs={"A": {"title": "A", "spec": "..."}},
            acceptance_criteria="x",
            handoff_note="",
        )


@pytest.mark.asyncio
async def test_partial_handoff_passes_note_through() -> None:
    tc = _FakeTC()
    arg = PartialPlanHandoffInput(
        phases=[[{"id": "A"}]],
        task_specs={"A": {"title": "A", "spec": "..."}},
        acceptance_criteria="x",
        handoff_note="covers half; gap = Y",
    )
    res = await submit_partial_plan_handoff.execute(arg, _ctx(tc, task_id="p"))
    assert res.is_error is False
    assert tc.calls[0][0] == "partial"
    assert tc.calls[0][-1] == "covers half; gap = Y"


# --------------------------------------------------------------------------- #
# submit_continue_to_work                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_continue_rejects_executor_role() -> None:
    """D3: continue is evaluator-only."""
    tc = _FakeTC()
    arg = ContinueToWorkInput(summary="gap")
    res = await submit_continue_to_work.execute(
        arg, _ctx(tc, task_id="x", role="executor")
    )
    assert res.is_error is True
    assert "evaluator-only" in res.output
    assert tc.calls == []


@pytest.mark.asyncio
async def test_continue_accepts_evaluator_role() -> None:
    tc = _FakeTC()
    arg = ContinueToWorkInput(summary="gap")
    res = await submit_continue_to_work.execute(
        arg, _ctx(tc, task_id="ev", role="evaluator")
    )
    assert res.is_error is False
    assert tc.calls == [("continue", "ev", "gap")]


# --------------------------------------------------------------------------- #
# read_task_details / read_task_graph                                         #
# --------------------------------------------------------------------------- #


def _seed_tc_with_tasks() -> _FakeTC:
    tc = _FakeTC()
    parent = Task(
        id="p", role="executor", title="Parent", spec="p spec",
        status=Status.AWAITING, acceptance_criteria="full criteria",
        children=["c1", "c2"],
    )
    c1 = Task(
        id="c1", role="executor", title="Child 1", spec="...",
        status=Status.DONE, summary="done1", parent_id="p",
    )
    c2 = Task(
        id="c2", role="executor", title="Child 2", spec="...",
        status=Status.RUNNING, parent_id="p",
    )
    grand = Task(
        id="grand", role="executor", title="Grandchild", spec="...",
        status=Status.RUNNING, parent_id="c1",
    )
    for t in (parent, c1, c2, grand):
        tc.graph.add(t)
    return tc


@pytest.mark.asyncio
async def test_read_task_details_returns_status_and_summary() -> None:
    tc = _seed_tc_with_tasks()
    res = await read_task_details.execute(
        ReadTaskDetailsInput(task_id="c1"), _ctx(tc)
    )
    assert res.is_error is False
    import json
    data = json.loads(res.output)
    assert data["status"] == "done"
    assert data["summary"] == "done1"
    assert data["title"] == "Child 1"


@pytest.mark.asyncio
async def test_read_task_details_unknown_id() -> None:
    tc = _seed_tc_with_tasks()
    res = await read_task_details.execute(
        ReadTaskDetailsInput(task_id="ghost"), _ctx(tc)
    )
    assert res.is_error is True


@pytest.mark.asyncio
async def test_read_task_graph_returns_only_direct_children() -> None:
    """Recursive opacity: never return grandchildren."""
    tc = _seed_tc_with_tasks()
    res = await read_task_graph.execute(
        ReadTaskGraphInput(task_id="p"), _ctx(tc)
    )
    assert res.is_error is False
    import json
    data = json.loads(res.output)
    ids = {c["id"] for c in data["children"]}
    assert ids == {"c1", "c2"}
    assert "grand" not in ids
