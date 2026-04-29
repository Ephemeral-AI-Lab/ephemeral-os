"""Stage 1 — TaskCenter creation primitives + RunController + Orchestrator.spawn.

Direct unit tests of the primitive layer with synthetic ``TaskCenter`` state,
per the roadmap Stage 1 acceptance criteria. The legacy lifecycle modules
exercise the same primitives indirectly; these tests pin the contracts so a
future refactor cannot silently break them.
"""

from __future__ import annotations

import pytest

from task_center.errors import TaskCenterError
from task_center.model import GeneratorRole, Status
from task_center.runtime import Orchestrator, RunController, TaskCenter


def _new_tc() -> TaskCenter:
    return TaskCenter()


# ---- _create_executor -------------------------------------------------------

def test_create_executor_assigns_internal_id_when_omitted() -> None:
    tc = _new_tc()
    task = tc._create_executor(
        input="hello",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    assert task.role == "executor"
    assert task.input == "hello"
    assert task.status is Status.READY
    assert task.task_center_harness_graph_id is None
    assert task.id.startswith("t")
    assert tc.graph.get(task.id) is task


def test_create_executor_honors_supplied_id() -> None:
    tc = _new_tc()
    task = tc._create_executor(
        input="x",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.PENDING,
        id="my-id",
    )
    assert task.id == "my-id"
    assert tc.graph.get("my-id") is task


def test_create_executor_rejects_duplicate_id() -> None:
    tc = _new_tc()
    tc._create_executor(
        input="x",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
        id="dup",
    )
    with pytest.raises(TaskCenterError, match="already in graph"):
        tc._create_executor(
            input="y",
            harness_graph_id=None,
            needs=frozenset(),
            status=Status.READY,
            id="dup",
        )


# ---- _create_planner -------------------------------------------------------

def test_create_planner_is_always_ready() -> None:
    tc = _new_tc()
    task = tc._create_planner(input="plan", harness_graph_id="g1")
    assert task.role == "planner"
    assert task.status is Status.READY
    assert task.task_center_harness_graph_id == "g1"


# ---- _create_verifier -------------------------------------------------------

def test_create_verifier_role_and_needs() -> None:
    tc = _new_tc()
    task = tc._create_verifier(
        input="verify",
        harness_graph_id="g1",
        needs=frozenset({"a"}),
        status=Status.PENDING,
    )
    assert task.role == "verifier"
    assert task.needs == frozenset({"a"})
    assert task.status is Status.PENDING


# ---- _create_evaluator ------------------------------------------------------

def test_create_evaluator_is_always_pending() -> None:
    tc = _new_tc()
    task = tc._create_evaluator(
        input="eval",
        harness_graph_id="g1",
        needs=frozenset({"a", "b"}),
    )
    assert task.role == "evaluator"
    assert task.status is Status.PENDING
    assert task.needs == frozenset({"a", "b"})


# ---- _create_advisor (Stage 1 stub) ----------------------------------------

def test_create_advisor_is_a_stage1_stub() -> None:
    tc = _new_tc()
    with pytest.raises(NotImplementedError, match="Stage 1 stub"):
        tc._create_advisor(input="ask", caller_id="t1")


# ---- _open_graph + Orchestrator.spawn ---------------------------------------

def test_open_graph_records_planner_in_both_legacy_and_new_slots() -> None:
    tc = _new_tc()
    root = tc._create_executor(
        input="root",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    graph = tc._open_graph(
        root_task_id=root.id,
        planner_id="p1",
        request_plan_note="please plan",
    )
    assert graph.root_task_id == root.id
    assert graph.planner_task_id == "p1"
    assert graph.planner == "p1"  # Stage 1 slot
    assert graph.root_goal == "root"
    assert graph.request_plan_note == "please plan"
    assert graph.prior_graph_id is None
    assert graph.dag_nodes == []
    assert graph.evaluator is None


def test_orchestrator_spawn_creates_graph_plus_planner() -> None:
    tc = _new_tc()
    root = tc._create_executor(
        input="root-goal",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    orch = Orchestrator.spawn(
        tc,
        root_task_id=root.id,
        request_plan_note="please plan",
    )
    assert orch.graph.root_task_id == root.id
    assert orch.planner.role == "planner"
    assert orch.planner.status is Status.READY
    assert orch.planner.task_center_harness_graph_id == orch.graph_id
    assert orch.graph.planner == orch.planner.id
    assert orch.graph.planner_task_id == orch.planner.id


def test_orchestrator_spawn_carries_prior_graph_id() -> None:
    tc = _new_tc()
    root = tc._create_executor(
        input="root",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    first = Orchestrator.spawn(
        tc,
        root_task_id=root.id,
        request_plan_note="seg1",
    )
    second = Orchestrator.spawn(
        tc,
        root_task_id=root.id,
        request_plan_note="seg2",
        prior_graph_id=first.graph_id,
    )
    assert second.graph.prior_graph_id == first.graph_id


# ---- RunController ----------------------------------------------------------

def test_run_controller_start_creates_root_executor() -> None:
    tc = _new_tc()
    rc = RunController(tc=tc)
    task = rc.start("hello world")
    assert task.role == "executor"
    assert task.status is Status.READY
    assert task.task_center_harness_graph_id is None
    assert task.input == "hello world"
    assert rc.root_task is task
    assert rc.is_done() is False


def test_run_controller_is_done_tracks_terminal_status() -> None:
    tc = _new_tc()
    rc = RunController(tc=tc)
    rc.start("x")
    assert rc.is_done() is False
    tc._mark_terminal(rc.root_task, Status.RUNNING) if False else None  # noqa
    # Drive through a legal terminal path: READY → RUNNING → DONE.
    tc.graph.transition(rc.root_task.id, Status.RUNNING)
    tc.graph.transition(rc.root_task.id, Status.DONE)
    assert rc.is_done() is True


# ---- GeneratorRole literal --------------------------------------------------

def test_generator_role_alias_imports() -> None:
    # Stage 1 deliverable: GeneratorRole literal is exported from
    # task_center.model so Stage 3 (DAG entry role validation) and Stage 6
    # (verifier sink rule) can rely on it.
    from typing import get_args

    assert set(get_args(GeneratorRole)) == {"executor", "verifier"}
