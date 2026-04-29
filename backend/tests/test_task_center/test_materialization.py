"""Orchestrator.materialize_full_plan / materialize_partial_plan validation."""

from __future__ import annotations

from task_center.model import Status
from task_center.runtime import Orchestrator, TaskCenter
from task_center.runtime.orchestrator import MaterializationFailure


def _new_orch_with_root() -> tuple[TaskCenter, Orchestrator]:
    tc = TaskCenter()
    root = tc._create_executor(
        input="root goal",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    orch = Orchestrator.spawn(
        tc, root_task_id=root.id, request_plan_note="please plan"
    )
    tc.graph.transition(orch.planner.id, Status.RUNNING)
    return tc, orch


def test_materialize_full_plan_creates_final_verifier_sink() -> None:
    tc, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "b", "deps": ["a"], "role": "executor"},
            {"id": "verify", "deps": ["a", "b"], "role": "verifier"},
        ],
        task_details={"a": "do A", "b": "do B", "verify": "verify A and B"},
    )

    assert err is None
    assert orch.graph.plan_shape == "full"
    assert orch.graph.dag_nodes == ["a", "b", "verify"]
    assert orch.graph.executor_task_ids == ["a", "b"]
    assert orch.terminal_verifier is tc.graph.get("verify")
    assert tc.graph.get("a").status is Status.READY
    assert tc.graph.get("b").status is Status.PENDING
    assert tc.graph.get("verify").status is Status.PENDING
    assert tc.graph.get("verify").needs == frozenset({"a", "b"})


def test_materialize_partial_plan_carries_what_to_do_next() -> None:
    tc, orch = _new_orch_with_root()
    err = orch.materialize_partial_plan(
        task_dep_graphs=[
            {"id": "shim", "deps": [], "role": "executor"},
            {"id": "smoke", "deps": ["shim"], "role": "executor"},
            {"id": "verify", "deps": ["shim", "smoke"], "role": "verifier"},
        ],
        task_details={
            "shim": "build shim",
            "smoke": "smoke test",
            "verify": "verify segment",
        },
        what_to_do_next="bulk fan-out after shim lands",
    )

    assert err is None
    assert orch.graph.plan_shape == "partial"
    assert orch.graph.what_to_do_next == "bulk fan-out after shim lands"
    assert tc.graph.get("verify").role == "verifier"
    assert orch.terminal_verifier is tc.graph.get("verify")


def test_materialize_role_defaults_to_executor_when_omitted() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": []},
            {"id": "verify", "deps": ["a"], "role": "verifier"},
        ],
        task_details={"a": "do A", "verify": "verify A"},
    )
    assert err is None


def test_empty_dag() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(task_dep_graphs=[], task_details={})
    assert isinstance(err, MaterializationFailure)
    assert err.code == "empty_dag"


def test_duplicate_ids() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "a", "deps": [], "role": "executor"},
        ],
        task_details={"a": "do A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "duplicate_ids"


def test_missing_details() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "verify", "deps": ["a"], "role": "verifier"},
        ],
        task_details={"a": "do A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "missing_details"


def test_unknown_role() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[{"id": "a", "deps": [], "role": "evaluator"}],
        task_details={"a": "do A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "unknown_role"


def test_unknown_dep() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": ["zzz"], "role": "executor"},
        ],
        task_details={"a": "do A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "unknown_dep"


def test_cycle() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": ["verify"], "role": "executor"},
            {"id": "verify", "deps": ["a"], "role": "verifier"},
        ],
        task_details={"a": "do A", "verify": "verify A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "cycle"


def test_missing_terminal_verifier() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[{"id": "a", "deps": [], "role": "executor"}],
        task_details={"a": "do A"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "terminal_verifier"


def test_final_verifier_must_depend_on_every_other_node() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "b", "deps": ["a"], "role": "executor"},
            {"id": "verify", "deps": ["b"], "role": "verifier"},
        ],
        task_details={"a": "do A", "b": "do B", "verify": "verify all"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "terminal_verifier_deps"


def test_id_collision_does_not_mutate_graph() -> None:
    tc, orch = _new_orch_with_root()
    root_id = orch.graph.root_task_id
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": root_id, "deps": [], "role": "executor"},
            {"id": "verify", "deps": [root_id], "role": "verifier"},
        ],
        task_details={root_id: "collides", "verify": "verify"},
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "id_collision"
    assert orch.graph.plan_shape is None
    assert orch.graph.dag_nodes == []
    assert orch.planner.status is Status.RUNNING
    assert tc.graph.get(root_id).input == "root goal"


def test_failed_materialization_does_not_mutate_graph() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(task_dep_graphs=[], task_details={})
    assert err is not None
    assert orch.graph.plan_shape is None
    assert orch.graph.dag_nodes == []
    assert orch.terminal_verifier is None
    assert orch.planner.status is Status.RUNNING


def test_partial_plan_failure_does_not_store_what_to_do_next() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_partial_plan(
        task_dep_graphs=[],
        task_details={},
        what_to_do_next="this should not be stored",
    )
    assert err is not None
    assert err.code == "empty_dag"
    assert orch.graph.what_to_do_next == ""
    assert orch.graph.plan_shape is None
