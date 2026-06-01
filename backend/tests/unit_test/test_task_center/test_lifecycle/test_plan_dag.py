"""Plan DAG helper tests (generators + reducers as one DAG).

``ordered_plan_tasks`` validates the combined generator+reducer plan and
enforces the structural gates that keep "every attempt has an exit AND all
work is judged" by construction: at least one reducer, and lane shape
(generators feed generators or terminal reducers; reducers directly gate
generators). ``ready_pending_plan_ids`` and ``dag_status`` drive RUN-stage
dispatch off the persisted task rows.
"""

from __future__ import annotations

import pytest

from workflow._core.primitives import WorkflowInvariantViolation
from workflow.attempt.plan_dag import (
    dag_status,
    ordered_plan_tasks,
    ready_pending_plan_ids,
)
from workflow.submissions import PlannedGeneratorTask, PlannedReducerTask


def _gen(local_id: str, needs: tuple[str, ...] = ()) -> PlannedGeneratorTask:
    return PlannedGeneratorTask(local_id, "executor", needs, f"do {local_id}")


def _red(local_id: str, needs: tuple[str, ...]) -> PlannedReducerTask:
    return PlannedReducerTask(local_id, needs, f"judge {local_id}")


def _task(task_id: str, status: str, needs: tuple[str, ...] = ()) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "needs": list(needs),
    }


# ---- ordered_plan_tasks: ordering ------------------------------------------


def test_ordered_plan_tasks_topological_and_stable():
    a = _gen("a")
    b = _gen("b", ("a",))
    c = _gen("c", ("a",))
    r = _red("r", ("b", "c"))

    ordered_gen, ordered_red = ordered_plan_tasks((b, c, a), (r,))

    assert ordered_gen == (a, b, c)
    assert ordered_red == (r,)


def test_ordered_plan_tasks_allows_generator_and_reducer_fan_in():
    a = _gen("a")
    b = _gen("b")
    c = _gen("c", ("a", "b"))
    d = _gen("d", ("a", "c"))
    r = _red("r", ("b", "d"))

    ordered_gen, ordered_red = ordered_plan_tasks((a, b, c, d), (r,))

    assert ordered_gen == (a, b, c, d)
    assert ordered_red == (r,)


def test_ordered_plan_tasks_allows_multiple_reducer_lanes():
    a = _gen("a")
    b = _gen("b", ("a",))
    c = _gen("c", ("a",))
    r1 = _red("r1", ("b",))
    r2 = _red("r2", ("c",))

    ordered_gen, ordered_red = ordered_plan_tasks((b, c, a), (r2, r1))

    assert ordered_gen == (a, b, c)
    assert ordered_red == (r1, r2)


# ---- ordered_plan_tasks: gate rules ----------------------------------------


def test_ordered_plan_tasks_rejects_missing_reducer():
    with pytest.raises(WorkflowInvariantViolation, match="at least one reducer"):
        ordered_plan_tasks((_gen("a"),), ())


def test_ordered_plan_tasks_rejects_unreachable_generator():
    # ``b`` has no downstream generator or reducer.
    a = _gen("a")
    b = _gen("b")
    r = _red("r", ("a",))

    with pytest.raises(WorkflowInvariantViolation, match="no downstream task needs"):
        ordered_plan_tasks((a, b), (r,))


def test_ordered_plan_tasks_rejects_reducer_without_generator_need():
    with pytest.raises(
        WorkflowInvariantViolation, match="must need at least one generator"
    ):
        ordered_plan_tasks((_gen("a"),), (_red("r", ()),))


def test_ordered_plan_tasks_rejects_reducer_needing_reducer():
    a = _gen("a")
    b = _gen("b", ("a",))
    r1 = _red("r1", ("b",))
    r2 = _red("r2", ("r1",))

    with pytest.raises(WorkflowInvariantViolation, match="cannot need reducer"):
        ordered_plan_tasks((a, b), (r1, r2))


def test_ordered_plan_tasks_rejects_generator_needing_reducer():
    a = _gen("a")
    b = _gen("b", ("r",))
    r = _red("r", ("a",))

    with pytest.raises(WorkflowInvariantViolation, match="cannot need reducer"):
        ordered_plan_tasks((a, b), (r,))


def test_ordered_plan_tasks_rejects_duplicate_local_id():
    a1 = _gen("a")
    a2 = _gen("a")
    r = _red("r", ("a",))

    with pytest.raises(WorkflowInvariantViolation, match="duplicate local ids"):
        ordered_plan_tasks((a1, a2), (r,))


def test_ordered_plan_tasks_rejects_duplicate_id_across_roles():
    # A generator and a reducer sharing a local id is still a duplicate.
    a = _gen("a")
    r = _red("a", ("a",))

    with pytest.raises(WorkflowInvariantViolation, match="duplicate local ids"):
        ordered_plan_tasks((a,), (r,))


def test_ordered_plan_tasks_rejects_unknown_needs():
    r = _red("r", ("missing",))

    with pytest.raises(WorkflowInvariantViolation, match="unknown needs"):
        ordered_plan_tasks((), (r,))


def test_ordered_plan_tasks_rejects_cycle():
    a = _gen("a", ("b",))
    b = _gen("b", ("a",))
    r = _red("r", ("a",))

    with pytest.raises(WorkflowInvariantViolation, match="dependency cycle"):
        ordered_plan_tasks((a, b), (r,))


# ---- ready_pending_plan_ids ------------------------------------------------


def test_ready_pending_plan_ids_requires_done_needs():
    records = [
        _task("a", "done"),
        _task("b", "pending", ("a",)),
        _task("c", "pending", ("b",)),
    ]

    assert ready_pending_plan_ids(records) == ("b",)


def test_ready_pending_plan_ids_includes_reducer_when_generators_done():
    records = [
        _task("g", "done"),
        _task("r", "pending", ("g",)),
    ]

    assert ready_pending_plan_ids(records) == ("r",)


# ---- dag_status ------------------------------------------------------------


def test_pending_dependents_of_failed_task_are_quiescent_not_started():
    records = [
        _task("a", "failed"),
        _task("b", "pending", ("a",)),
        _task("c", "pending", ("b",)),
        _task("d", "running"),
    ]

    state = dag_status(records)
    assert not state.all_quiescent


def test_pending_dependents_of_failed_task_close_after_siblings_finish():
    records = [
        _task("a", "failed"),
        _task("b", "pending", ("a",)),
        _task("c", "pending", ("b",)),
        _task("d", "done"),
    ]

    state = dag_status(records)
    assert state.all_quiescent
    assert not state.all_done
    assert state.any_failed_or_blocked


def test_all_done_plan_is_quiescent_and_done():
    records = [
        _task("g", "done"),
        _task("r", "done", ("g",)),
    ]

    state = dag_status(records)
    assert state.all_quiescent
    assert state.all_done
    assert not state.any_failed_or_blocked


def test_waiting_workflow_is_not_quiescent_or_done():
    records = [_task("a", "waiting_workflow")]

    state = dag_status(records)
    assert not state.all_quiescent
    assert not state.all_done
