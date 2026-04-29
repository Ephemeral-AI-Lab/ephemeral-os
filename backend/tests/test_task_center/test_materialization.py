"""Stage 3 — Orchestrator.materialize_full_plan / materialize_partial_plan.

Validation matrix: one test per ``MaterializationFailure.code`` value plus
happy-path coverage for both terminals (full + partial). Pinned by the
roadmap as: "New tests in test_materialization.py cover one case per
MaterializationFailure.code value (matrix test)."
"""

from __future__ import annotations

from task_center.model import Status
from task_center.runtime import Orchestrator, TaskCenter
from task_center.runtime.orchestrator import MaterializationFailure


def _new_orch_with_root() -> tuple[TaskCenter, Orchestrator]:
    """Build a fresh TaskCenter with root_exec + a planner-led graph.

    The planner is driven to RUNNING (the state the dispatcher would have
    transitioned it to before invoking its terminal tool), so that
    ``materialize_*_plan``'s internal RUNNING→HANDOFF transition lands.
    """
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


# ---- Happy paths ------------------------------------------------------------


def test_materialize_full_plan_creates_executors_plus_evaluator() -> None:
    tc, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "b", "deps": ["a"], "role": "executor"},
        ],
        task_details={"a": "do A", "b": "do B"},
        evaluation_specification="check both landed",
    )
    assert err is None
    assert orch.graph.plan_shape == "full"
    a = tc.graph.get("a")
    b = tc.graph.get("b")
    assert a.role == "executor"
    assert a.status is Status.READY  # no deps
    assert b.role == "executor"
    assert b.status is Status.PENDING  # has deps
    assert b.needs == frozenset({"a"})
    evaluator = orch.evaluator
    assert evaluator is not None
    assert evaluator.role == "evaluator"
    assert evaluator.input == "check both landed"
    assert evaluator.needs == frozenset({"b"})  # b is the sink


def test_materialize_partial_plan_carries_what_to_do_next() -> None:
    tc, orch = _new_orch_with_root()
    err = orch.materialize_partial_plan(
        task_dep_graphs=[
            {"id": "shim", "deps": [], "role": "executor"},
            {"id": "verify", "deps": ["shim"], "role": "verifier"},
            {"id": "smoke", "deps": ["verify"], "role": "executor"},
        ],
        task_details={"shim": "build shim", "verify": "verify shim", "smoke": "smoke test"},
        what_to_do_next="bulk fan-out after shim lands",
        evaluation_specification="checkpoint reached",
    )
    assert err is None
    assert orch.graph.plan_shape == "partial"
    assert orch.graph.what_to_do_next == "bulk fan-out after shim lands"
    assert tc.graph.get("verify").role == "verifier"
    # Verifier is mid-graph; smoke is the sink so it gates the evaluator.
    assert orch.evaluator is not None
    assert orch.evaluator.needs == frozenset({"smoke"})


def test_materialize_role_defaults_to_executor_when_omitted() -> None:
    """A DAG entry without an explicit role is treated as executor.

    Required for the legacy ``submit_plan_handoff`` alias path.
    """
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[{"id": "a", "deps": []}],
        task_details={"a": "do A"},
        evaluation_specification="ok",
    )
    assert err is None


# ---- Validation matrix ------------------------------------------------------


def test_empty_dag() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[],
        task_details={},
        evaluation_specification="never reached",
    )
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
        evaluation_specification="x",
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "duplicate_ids"


def test_missing_details() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "b", "deps": [], "role": "executor"},
        ],
        task_details={"a": "do A"},  # missing 'b'
        evaluation_specification="x",
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "missing_details"


def test_unknown_role() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[{"id": "a", "deps": [], "role": "evaluator"}],
        task_details={"a": "do A"},
        evaluation_specification="x",
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
        evaluation_specification="x",
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "unknown_dep"


def test_cycle() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": ["b"], "role": "executor"},
            {"id": "b", "deps": ["a"], "role": "executor"},
        ],
        task_details={"a": "do A", "b": "do B"},
        evaluation_specification="x",
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "cycle"


def test_verifier_sink() -> None:
    """Verifier nodes cannot be DAG sinks — would gate-conflict with the
    auto-spawned evaluator."""
    _, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[
            {"id": "a", "deps": [], "role": "executor"},
            {"id": "v", "deps": ["a"], "role": "verifier"},
        ],
        task_details={"a": "do A", "v": "verify A"},
        evaluation_specification="x",
    )
    assert isinstance(err, MaterializationFailure)
    assert err.code == "verifier_sink"


# ---- Failure does not mutate the graph -------------------------------------


def test_failed_materialization_does_not_mutate_graph() -> None:
    """Phase 1 lenient rule: a MaterializationFailure does not consume the
    advisor accept and (more concretely here) does not partially populate
    the graph. The planner still has zero children + no evaluator."""
    tc, orch = _new_orch_with_root()
    err = orch.materialize_full_plan(
        task_dep_graphs=[],
        task_details={},
        evaluation_specification="x",
    )
    assert err is not None
    assert orch.graph.plan_shape is None
    assert orch.graph.dag_nodes == []
    assert orch.graph.evaluator is None
    # The planner should still be RUNNING (the materialize body did not
    # transition it to HANDOFF because validation rejected the DAG).
    assert orch.planner.status is Status.RUNNING


# ---- Partial plan stores what_to_do_next on success but not on failure -----


def test_partial_plan_failure_does_not_store_what_to_do_next() -> None:
    _, orch = _new_orch_with_root()
    err = orch.materialize_partial_plan(
        task_dep_graphs=[],  # empty → empty_dag
        task_details={},
        what_to_do_next="this should not be stored",
        evaluation_specification="x",
    )
    assert err is not None
    assert err.code == "empty_dag"
    assert orch.graph.what_to_do_next == ""
    assert orch.graph.plan_shape is None
