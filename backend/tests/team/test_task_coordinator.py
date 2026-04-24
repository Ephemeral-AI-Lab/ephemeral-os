"""Unit tests for TaskCoordinator core match-block cases.

Exercises the coordinator against a real ``TaskGraph`` + a tiny fake store
that records ``persist`` calls. Tests assert on emitted events and final
graph state — not SQL call sequences — so the shape of the persistence
layer can evolve without churn here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.registry import get_definition
from team.core.models import (
    BudgetConfig,
    BudgetState,
    Plan,
    Task,
    TaskDefinition,
    TaskStatus,
    TaskStatusUpdate,
)
from team.definitions import register_all as register_team_builtins
from team.planning.expander import PlanExpansionOutcome, ReplanApplyOutcome
from team.runtime.task_coordinator import TaskCoordinator
from team.runtime.task_graph import GraphMutation, TaskGraph, TaskInsert


if get_definition("developer") is None:
    register_team_builtins()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.READY,
    agent_name: str = "developer",
    fired_by_task_id: str | None = None,
    parent_id: str | None = None,
    deps: list[str] | None = None,
) -> Task:
    spec = {
        "goal": "do something",
        "detail": "Do the assigned work.",
        "acceptance_criteria": "Submit the terminal outcome.",
    }
    return Task(
        id=task_id,
        team_run_id="run-1",
        spec=spec,
        agent=agent_name,
        status=status,
        parent_id=parent_id,
        deps=deps or [],
        fired_by_task_id=fired_by_task_id,
    )


class FakeStore:
    """Captures every ``persist`` call; ``mark_running`` is DB-atomic so it
    simply returns a SimpleNamespace mimicking a TaskRecord."""

    def __init__(self) -> None:
        self.persist_calls: list[GraphMutation] = []
        self.mark_running = AsyncMock(return_value=None)

    async def persist(self, mutation: GraphMutation) -> None:
        self.persist_calls.append(mutation)


class FakeBudget:
    def __init__(self) -> None:
        self.budgets = BudgetConfig()
        self.budget_state = BudgetState()

    def require_replan_capacity(self) -> None:
        pass

    def bump_replan_counters(self) -> None:
        pass

    def emit_update(self) -> None:
        pass


class FakeExpander:
    def __init__(self, outcome: PlanExpansionOutcome | None = None) -> None:
        self.expand_outcome = outcome or PlanExpansionOutcome(mutation=GraphMutation.empty())
        self.replan_outcome: ReplanApplyOutcome | None = None
        self.expand_calls: list[tuple[Task, object]] = []
        self.replan_calls: list[dict[str, object]] = []

    def expand_submitted_plan(self, task: Task, plan: object) -> PlanExpansionOutcome:
        self.expand_calls.append((task, plan))
        return self.expand_outcome

    def apply_replan(
        self, *, replan_task: Task, add_tasks: list, cancel_ids: list
    ) -> ReplanApplyOutcome:
        self.replan_calls.append(
            {"replan_task": replan_task, "add_tasks": add_tasks, "cancel_ids": cancel_ids}
        )
        if self.replan_outcome is None:
            return ReplanApplyOutcome(mutation=GraphMutation.empty())
        return self.replan_outcome


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, task_id: str) -> None:
        self.enqueued.append(task_id)


def _make_coordinator(
    graph: TaskGraph,
    store: FakeStore,
    *,
    budget: FakeBudget | None = None,
    expander: FakeExpander | None = None,
    fail_fast: AsyncMock | None = None,
    cancel_event: asyncio.Event | None = None,
    events: list | None = None,
) -> tuple[TaskCoordinator, FakeQueue]:
    if events is None:
        events = []
    coord = TaskCoordinator(
        team_run_id="run-1",
        graph=graph,
        store=store,  # type: ignore[arg-type]
        budget=budget or FakeBudget(),
        expander=expander or FakeExpander(),  # type: ignore[arg-type]
        emit_event=lambda e: events.append(e),
        fail_fast=fail_fast or AsyncMock(),
        cancel_event=cancel_event,
    )
    queue = FakeQueue()
    coord.bind_queue(queue)  # type: ignore[arg-type]
    return coord, queue


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_marks_done_and_enqueues_newly_ready_deps():
    task = _task("main", status=TaskStatus.RUNNING)
    dep = _task("dep-1", status=TaskStatus.PENDING, deps=["main"])
    graph = TaskGraph({"main": task, "dep-1": dep})
    store = FakeStore()
    coord, queue = _make_coordinator(graph, store)

    await coord.handle(TaskStatusUpdate(task_id="main", status=TaskStatus.DONE, summary="ok"))

    assert task.status is TaskStatus.DONE
    assert dep.status is TaskStatus.READY
    assert "dep-1" in queue.enqueued
    assert len(store.persist_calls) == 1


@pytest.mark.asyncio
async def test_success_promotes_parent_with_synthesized_child_summary(monkeypatch):
    monkeypatch.setattr("agents.registry.has_role", lambda name, role: False)

    parent = _task("parent", status=TaskStatus.EXPANDED, agent_name="team_planner")
    parent.plan = Plan()
    child = _task("child", status=TaskStatus.RUNNING, parent_id="parent")
    graph = TaskGraph({"parent": parent, "child": child})
    store = FakeStore()
    coord, _ = _make_coordinator(graph, store)

    await coord.handle(
        TaskStatusUpdate(task_id="child", status=TaskStatus.DONE, summary="child delivered")
    )

    assert child.status is TaskStatus.DONE
    assert parent.status is TaskStatus.DONE
    assert parent.summary == "child delivered"


@pytest.mark.asyncio
async def test_synthesized_parent_summary_prefers_terminal_validator(monkeypatch):
    monkeypatch.setattr(
        "agents.registry.has_role",
        lambda name, role: name == "validator" and role == "reviewer",
    )

    parent = _task("parent", status=TaskStatus.EXPANDED, agent_name="team_planner")
    dev = _task("dev", status=TaskStatus.DONE, agent_name="developer", parent_id="parent")
    dev.summary = "developer summary"
    validator = _task(
        "validator-task", status=TaskStatus.RUNNING, agent_name="validator", parent_id="parent"
    )
    validator.summary = "validator summary"
    graph = TaskGraph({"parent": parent, "dev": dev, "validator-task": validator})
    store = FakeStore()
    fail_fast = AsyncMock()
    coord, _ = _make_coordinator(graph, store, fail_fast=fail_fast)

    await coord.handle(
        TaskStatusUpdate(
            task_id="validator-task", status=TaskStatus.DONE, summary="validator summary"
        )
    )

    assert parent.status is TaskStatus.DONE
    assert parent.summary == "validator summary"
    assert isinstance(parent.plan, Plan)
    fail_fast.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_marks_failed_and_calls_fail_fast_once():
    task = _task("failing", status=TaskStatus.RUNNING)
    graph = TaskGraph({"failing": task})
    store = FakeStore()
    cancel_event = asyncio.Event()
    fail_fast = AsyncMock()
    coord, _ = _make_coordinator(graph, store, fail_fast=fail_fast, cancel_event=cancel_event)

    await coord.handle(
        TaskStatusUpdate(task_id="failing", status=TaskStatus.FAILED, summary="boom")
    )

    assert task.status is TaskStatus.FAILED
    assert task.failure_reason == "boom"
    fail_fast.assert_awaited_once_with("boom")

    cancel_event.set()
    await coord.handle(
        TaskStatusUpdate(task_id="failing", status=TaskStatus.FAILED, summary="boom")
    )
    # Still only the first call — second is idempotent under cancel_event.
    assert fail_fast.await_count == 1


@pytest.mark.asyncio
async def test_expanded_with_plan_marks_parent_expanded_and_enqueues_ready_children():
    planner = _task("planner", status=TaskStatus.RUNNING, agent_name="team_planner")
    child_a = _task("child-a", status=TaskStatus.READY, parent_id="planner")
    child_b = _task("child-b", status=TaskStatus.READY, parent_id="planner")
    graph = TaskGraph({"planner": planner})  # children will be inserted by mutation

    insert_mutation = GraphMutation(
        inserts=(TaskInsert(child_a), TaskInsert(child_b)),
    )
    plan = Plan(
        tasks=[
            TaskDefinition(
                id="child-a",
                spec={"goal": "a", "detail": "a", "acceptance_criteria": "submit"},
                agent="developer",
            ),
            TaskDefinition(
                id="child-b",
                spec={"goal": "b", "detail": "b", "acceptance_criteria": "submit"},
                agent="developer",
            ),
        ]
    )
    expander = FakeExpander(PlanExpansionOutcome(mutation=insert_mutation, new_tasks=(child_a, child_b)))
    store = FakeStore()
    coord, queue = _make_coordinator(graph, store, expander=expander)

    await coord.handle(
        TaskStatusUpdate(task_id="planner", status=TaskStatus.EXPANDED, plan=plan)
    )

    assert planner.status is TaskStatus.EXPANDED
    assert graph.get("child-a") is child_a
    assert "child-a" in queue.enqueued and "child-b" in queue.enqueued


@pytest.mark.asyncio
async def test_request_replan_spawns_replanner_and_enqueues_it(monkeypatch):
    monkeypatch.setattr(
        "agents.registry.find_by_role",
        lambda role: [SimpleNamespace(name="team_replanner")] if role == "replanner" else [],
    )
    monkeypatch.setattr(
        "team.runtime.task_graph._has_replanner_role", lambda name: name == "team_replanner"
    )

    origin = _task("broken", status=TaskStatus.RUNNING)
    graph = TaskGraph({"broken": origin})
    store = FakeStore()
    coord, queue = _make_coordinator(graph, store)

    await coord.handle(
        TaskStatusUpdate(
            task_id="broken", status=TaskStatus.REQUEST_REPLAN, summary="needs fixing"
        )
    )

    assert origin.status is TaskStatus.REQUEST_REPLAN
    # A new replanner should be in the graph and enqueued.
    replanners = [t for t in graph.tasks.values() if t.agent == "team_replanner"]
    assert len(replanners) == 1
    replanner = replanners[0]
    assert replanner.fired_by_task_id == "broken"
    assert replanner.id in queue.enqueued
