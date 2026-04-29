"""Stage 2 — verifier lifecycle (degraded — no fix-executor yet).

Stage 2 of the four-role roadmap lands the verifier success/failure routers.
Failure cascade-fails dependents (the Stage 6 ``Status.FIXING`` →
fix-executor flow lands later). Stage 2 also adds ``Status.FIXING`` to the
enum even though it is not yet entered — kept here so that DB migration
work in Stage 8 sees the literal value.
"""

from __future__ import annotations

import pytest

from task_center.errors import TaskCenterError
from task_center.model import Status
from task_center.runtime import TaskCenter


def _build_two_node_graph(tc: TaskCenter) -> tuple[str, str, str]:
    """Construct a parent → verifier → downstream chain by hand.

    Returns (parent_executor_id, verifier_id, downstream_executor_id).
    The verifier is left in RUNNING (the state the real dispatcher
    transitions tasks through before they call their terminal tool), so
    the verifier success/failure transitions land legally.
    """
    parent = tc._create_executor(
        input="parent",
        harness_graph_id="g1",
        needs=frozenset(),
        status=Status.READY,
    )
    # Drive parent to DONE so the verifier becomes the failure point.
    tc.graph.transition(parent.id, Status.RUNNING)
    tc.graph.transition(parent.id, Status.DONE)

    verifier = tc._create_verifier(
        input="check parent",
        harness_graph_id="g1",
        needs=frozenset({parent.id}),
        status=Status.READY,
    )
    tc.graph.transition(verifier.id, Status.RUNNING)
    downstream = tc._create_executor(
        input="downstream",
        harness_graph_id="g1",
        needs=frozenset({verifier.id}),
        status=Status.PENDING,
    )
    return parent.id, verifier.id, downstream.id


# ---- Status.FIXING enum value ----------------------------------------------


def test_status_fixing_exists_and_serializes() -> None:
    assert Status.FIXING.value == "fixing"
    assert Status("fixing") is Status.FIXING


# ---- submit_verification_success -------------------------------------------


def test_verification_success_marks_verifier_done() -> None:
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)

    tc.submit_verification_success(verifier_id, "checks pass")

    verifier = tc.graph.get(verifier_id)
    assert verifier.status is Status.DONE
    assert verifier.summaries[-1].kind == "success"
    assert verifier.summaries[-1].text == "checks pass"

    # Downstream should still be PENDING — promotion happens on the next
    # dispatcher tick. Stage 2 only requires the success transition.
    assert tc.graph.get(downstream_id).status is Status.PENDING


def test_verification_success_rejects_wrong_role() -> None:
    tc = TaskCenter()
    executor = tc._create_executor(
        input="x",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    with pytest.raises(TaskCenterError, match="is not verifier"):
        tc.submit_verification_success(executor.id, "wrong role")


# ---- submit_verification_failure (degraded path) ---------------------------


def test_verification_failure_cascades_to_dependents() -> None:
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)

    tc.submit_verification_failure(verifier_id, "checks failed")

    verifier = tc.graph.get(verifier_id)
    assert verifier.status is Status.FAILED
    assert verifier.summaries[-1].kind == "failure"
    assert verifier.summaries[-1].text == "checks failed"

    downstream = tc.graph.get(downstream_id)
    assert downstream.status is Status.FAILED
    assert downstream.summaries[-1].kind == "dependency_blocked"
    assert verifier_id in downstream.summaries[-1].text


def test_verification_failure_cascades_to_verifier_dependent() -> None:
    """Generators (executors AND verifiers) downstream of a failed verifier
    cascade-fail together. Pins the four-role behavior of
    ``dependency_blocked_descendants``.
    """
    tc = TaskCenter()
    parent = tc._create_verifier(
        input="parent verify",
        harness_graph_id="g1",
        needs=frozenset(),
        status=Status.READY,
    )
    tc.graph.transition(parent.id, Status.RUNNING)
    downstream_verifier = tc._create_verifier(
        input="downstream verify",
        harness_graph_id="g1",
        needs=frozenset({parent.id}),
        status=Status.PENDING,
    )

    tc.submit_verification_failure(parent.id, "boom")

    assert tc.graph.get(parent.id).status is Status.FAILED
    assert tc.graph.get(downstream_verifier.id).status is Status.FAILED


def test_verification_failure_rejects_wrong_role() -> None:
    tc = TaskCenter()
    executor = tc._create_executor(
        input="x",
        harness_graph_id=None,
        needs=frozenset(),
        status=Status.READY,
    )
    with pytest.raises(TaskCenterError, match="is not verifier"):
        tc.submit_verification_failure(executor.id, "wrong role")


# ---- silent termination -----------------------------------------------------


def test_verifier_silent_termination_routes_to_failure() -> None:
    """A verifier task that exits without a terminal call should fail
    through the same path as ``submit_verification_failure``."""
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)

    verifier = tc.graph.get(verifier_id)
    # The fixture leaves the verifier in RUNNING; silent termination is
    # the dispatcher's reaction to the agent exiting from RUNNING.
    tc._handle_silent_termination(verifier, "agent crashed")

    assert tc.graph.get(verifier_id).status is Status.FAILED
    assert tc.graph.get(downstream_id).status is Status.FAILED
