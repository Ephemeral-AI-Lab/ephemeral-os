"""Unit tests for ``task_center.task`` — Task dataclass + Status enum."""

from __future__ import annotations

from task_center import (
    PlanValidationError,
    Status,
    Task,
    TaskCenterError,
    TaskSummary,
)


def test_status_enum_has_exactly_six_values() -> None:
    expected = ["pending", "ready", "running", "handoff", "done", "failed"]
    assert [s.value for s in Status] == expected


def test_status_string_membership() -> None:
    assert Status.PENDING == "pending"
    assert Status.HANDOFF == "handoff"
    assert Status.DONE.value == "done"


def test_task_constructs_with_minimum_fields() -> None:
    task = Task(
        id="t1",
        role="executor",
        input="Do the thing.",
        status=Status.READY,
    )
    assert task.id == "t1"
    assert task.role == "executor"
    assert task.status is Status.READY
    assert task.task_center_harness_graph_id is None
    assert task.needs == frozenset()
    assert task.summaries == []
    assert isinstance(task.created_at, float)


def test_task_summaries_are_independent_per_instance() -> None:
    a = Task(id="a", role="executor", input="...", status=Status.PENDING)
    b = Task(id="b", role="executor", input="...", status=Status.PENDING)
    a.summaries.append(TaskSummary(kind="success", text="ok", source_task_id="a"))
    assert b.summaries == []
    assert a.summaries[0].text == "ok"


def test_task_role_widens_to_planner() -> None:
    planner = Task(id="p", role="planner", input="...", status=Status.READY)
    assert planner.role == "planner"


def test_task_summary_holds_kind_text_source() -> None:
    summary = TaskSummary(kind="failure", text="boom", source_task_id="t")
    assert summary.kind == "failure"
    assert summary.text == "boom"
    assert summary.source_task_id == "t"
    assert isinstance(summary.created_at, float)


def test_error_hierarchy() -> None:
    assert issubclass(PlanValidationError, TaskCenterError)
    assert issubclass(TaskCenterError, Exception)
