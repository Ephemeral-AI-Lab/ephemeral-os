"""Final verifier closure behavior."""

from __future__ import annotations

import pytest

from task_center.errors import TaskCenterError
from task_center.model import Status, TaskSummary
from task_center.runtime import Orchestrator, TaskCenter


def _setup_terminal_verifier(tc: TaskCenter) -> str:
    root = tc._create_executor(
        input="root", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    tc.graph.transition(root.id, Status.RUNNING)
    tc.graph.transition(root.id, Status.HANDOFF)
    orch = Orchestrator.spawn(
        tc, root_task_id=root.id, request_plan_note="please plan"
    )
    tc.graph.transition(orch.planner.id, Status.RUNNING)
    orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "child", "deps": [], "role": "executor"},
            {"id": "verify", "deps": ["child"], "role": "verifier"},
        ],
        task_details={"child": "do work", "verify": "verify work"},
    )
    child = tc.graph.get("child")
    tc.graph.transition(child.id, Status.RUNNING)
    tc.graph.transition(child.id, Status.DONE)
    verifier = tc.graph.get("verify")
    tc.graph.transition(verifier.id, Status.READY)
    tc.graph.transition(verifier.id, Status.RUNNING)
    return verifier.id


def test_submit_verification_success_closes_graph() -> None:
    tc = TaskCenter()
    verifier_id = _setup_terminal_verifier(tc)

    tc.submit_verification_success(verifier_id, "approved")

    verifier = tc.graph.get(verifier_id)
    assert verifier.status is Status.DONE
    assert verifier.summaries[-1].kind == "success"
    assert verifier.task_center_harness_graph_id is not None
    orch = Orchestrator(graph_id=verifier.task_center_harness_graph_id, tc=tc)
    assert tc.graph.get(orch.planner.id).status is Status.DONE
    assert orch.root_task.status is Status.DONE


def test_submit_verification_success_rejects_executor() -> None:
    tc = TaskCenter()
    executor = tc._create_executor(
        input="x", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    with pytest.raises(TaskCenterError, match="is not verifier"):
        tc.submit_verification_success(executor.id, "wrong role")


def test_orchestrator_close_success_marks_planner_and_root_done() -> None:
    tc = TaskCenter()
    verifier_id = _setup_terminal_verifier(tc)
    verifier = tc.graph.get(verifier_id)
    assert verifier.task_center_harness_graph_id is not None
    orch = Orchestrator(graph_id=verifier.task_center_harness_graph_id, tc=tc)
    verifier.summaries.append(
        TaskSummary(kind="success", text="approved", source_task_id=verifier_id)
    )
    tc._mark_terminal(verifier, Status.DONE)

    orch.close_success()

    assert tc.graph.get(orch.planner.id).status is Status.DONE
    assert orch.root_task.status is Status.DONE


def test_orchestrator_close_failure_marks_planner_and_root_failed() -> None:
    tc = TaskCenter()
    verifier_id = _setup_terminal_verifier(tc)
    verifier = tc.graph.get(verifier_id)
    assert verifier.task_center_harness_graph_id is not None
    orch = Orchestrator(graph_id=verifier.task_center_harness_graph_id, tc=tc)
    verifier.summaries.append(
        TaskSummary(kind="failure", text="cannot meet goal", source_task_id=verifier_id)
    )
    tc._mark_terminal(verifier, Status.FAILED)

    orch.close_failure()

    assert tc.graph.get(orch.planner.id).status is Status.FAILED
    assert orch.root_task.status is Status.FAILED
