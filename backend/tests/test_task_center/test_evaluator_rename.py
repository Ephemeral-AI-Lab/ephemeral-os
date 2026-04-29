"""Stage 7 — `submit_evaluation_success` terminal rename.

Evaluator gets the four-role-correct terminal name. The legacy
``submit_task_success`` stays in TaskCenter as a polymorphic shim so
existing scripted spawns + tests do not regress (they call
``tc.submit_task_success(eval_id)`` directly).
"""

from __future__ import annotations

import pytest

from task_center.errors import TaskCenterError
from task_center.model import Status
from task_center.runtime import TaskCenter


def _setup_evaluator(tc: TaskCenter) -> str:
    """Build a minimal scenario where calling the success terminal lands."""
    root = tc._create_executor(
        input="root", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    tc.graph.transition(root.id, Status.RUNNING)
    tc.graph.transition(root.id, Status.HANDOFF)
    from task_center.runtime import Orchestrator

    orch = Orchestrator.spawn(
        tc, root_task_id=root.id, request_plan_note="please plan"
    )
    tc.graph.transition(orch.planner.id, Status.RUNNING)
    orch.materialize_full_plan(
        task_dep_graphs=[{"id": "child", "deps": [], "role": "executor"}],
        task_details={"child": "do work"},
        evaluation_specification="check",
    )
    # Drive the child + evaluator into RUNNING (mirroring the dispatcher).
    child = tc.graph.get("child")
    tc.graph.transition(child.id, Status.RUNNING)
    tc.graph.transition(child.id, Status.DONE)
    evaluator = orch.evaluator
    assert evaluator is not None
    tc.graph.transition(evaluator.id, Status.READY)
    tc.graph.transition(evaluator.id, Status.RUNNING)
    return evaluator.id


def test_submit_evaluation_success_closes_graph() -> None:
    tc = TaskCenter()
    eval_id = _setup_evaluator(tc)

    tc.submit_evaluation_success(eval_id, "approved")

    assert tc.graph.get(eval_id).status is Status.DONE
    assert tc.graph.get(eval_id).summaries[-1].kind == "success"


def test_submit_evaluation_success_rejects_executor() -> None:
    tc = TaskCenter()
    executor = tc._create_executor(
        input="x", harness_graph_id=None, needs=frozenset(), status=Status.READY
    )
    with pytest.raises(TaskCenterError, match="is not evaluator"):
        tc.submit_evaluation_success(executor.id, "wrong role")


def test_legacy_submit_task_success_still_works_for_evaluator() -> None:
    """Backward compat: existing scripted spawns use tc.submit_task_success
    polymorphically. Stage 7 keeps that shim alive while agent prompts
    migrate to the new tool name."""
    tc = TaskCenter()
    eval_id = _setup_evaluator(tc)

    tc.submit_task_success(eval_id, "legacy path approved")
    assert tc.graph.get(eval_id).status is Status.DONE


# ---- Stage 7 — Orchestrator.close_success / close_failure ----------------


def test_orchestrator_close_success_marks_planner_and_root_done() -> None:
    """Full-plan closure facade on Orchestrator delegates to evaluator
    lifecycle and mirrors the state transitions of the legacy path."""
    from task_center.runtime import Orchestrator

    tc = TaskCenter()
    eval_id = _setup_evaluator(tc)
    eval_task = tc.graph.get(eval_id)
    assert eval_task.task_center_harness_graph_id is not None
    orch = Orchestrator(graph_id=eval_task.task_center_harness_graph_id, tc=tc)
    # Drive evaluator DONE (the lifecycle helper assumes it's already DONE).
    from task_center.model import TaskSummary

    eval_task.summaries.append(
        TaskSummary(kind="success", text="approved", source_task_id=eval_id)
    )
    tc._mark_terminal(eval_task, Status.DONE)

    orch.close_success()

    # Planner DONE, root_task DONE.
    assert tc.graph.get(orch.planner.id).status is Status.DONE
    assert orch.root_task.status is Status.DONE


def test_orchestrator_close_failure_marks_planner_and_root_failed() -> None:
    from task_center.runtime import Orchestrator

    tc = TaskCenter()
    eval_id = _setup_evaluator(tc)
    eval_task = tc.graph.get(eval_id)
    assert eval_task.task_center_harness_graph_id is not None
    orch = Orchestrator(graph_id=eval_task.task_center_harness_graph_id, tc=tc)
    from task_center.model import TaskSummary

    eval_task.summaries.append(
        TaskSummary(
            kind="evaluation_failure",
            text="cannot meet goal",
            source_task_id=eval_id,
        )
    )
    tc._mark_terminal(eval_task, Status.FAILED)

    orch.close_failure()

    assert tc.graph.get(orch.planner.id).status is Status.FAILED
    assert orch.root_task.status is Status.FAILED
