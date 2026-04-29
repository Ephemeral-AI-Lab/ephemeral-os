"""Stage 2/6 — verifier lifecycle.

Stage 2 added the success/failure dispatchers in their degraded form.
Stage 6 replaces failure cascade-fail with: verifier → FIXING + spawn
fix-executor. Fix-executor success → verifier re-runs (FIXING → READY).
Fix-executor failure → verifier FAILED + cascade.
"""

from __future__ import annotations

import pytest

from task_center.errors import TaskCenterError
from task_center.model import Status
from task_center.runtime import TaskCenter


def _build_two_node_graph(tc: TaskCenter) -> tuple[str, str, str]:
    """Construct a parent → verifier → downstream chain.

    Returns (parent_executor_id, verifier_id, downstream_executor_id). The
    verifier is left in RUNNING (the state the real dispatcher transitions
    tasks through before they call their terminal tool).
    """
    parent = tc._create_executor(
        input="parent",
        harness_graph_id="g1",
        needs=frozenset(),
        status=Status.READY,
    )
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
    # dispatcher tick.
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


# ---- Stage 6 — submit_verification_failure → FIXING + fix-executor ---------


def test_verification_failure_transitions_to_fixing_and_spawns_fix_executor() -> None:
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)

    tc.submit_verification_failure(verifier_id, "checks failed: typo on line 42")

    verifier = tc.graph.get(verifier_id)
    assert verifier.status is Status.FIXING
    assert verifier.summaries[-1].kind == "failure"
    # Downstream must NOT cascade-fail while the fix attempt is in flight.
    assert tc.graph.get(downstream_id).status is Status.PENDING
    # A fix-executor was spawned in the same harness graph.
    fix_execs = [
        t
        for t in tc.graph.tasks.values()
        if t.spawn_reason == "fix_verification" and t.fix_target_id == verifier_id
    ]
    assert len(fix_execs) == 1
    fix = fix_execs[0]
    assert fix.role == "executor"
    assert fix.status is Status.READY
    # Synthesized input includes the failure summary, the verifier's input,
    # and dep summaries.
    assert "checks failed: typo on line 42" in fix.input
    assert "check parent" in fix.input  # verifier's task input
    assert "FIX MODE" in fix.input


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


# ---- Stage 6 — fix-executor success: verifier re-runs ----------------------


def test_fix_executor_success_re_runs_verifier() -> None:
    tc = TaskCenter()
    _, verifier_id, _ = _build_two_node_graph(tc)
    tc.submit_verification_failure(verifier_id, "boom")

    fix = next(
        t
        for t in tc.graph.tasks.values()
        if t.spawn_reason == "fix_verification"
    )
    # Drive the fix-executor through RUNNING (mirroring dispatcher).
    tc.graph.transition(fix.id, Status.RUNNING)
    tc.submit_task_success(fix.id, "fixed the typo")

    # Verifier re-runs: transitions FIXING → READY.
    verifier = tc.graph.get(verifier_id)
    assert verifier.status is Status.READY
    # Fix-executor itself is DONE.
    assert tc.graph.get(fix.id).status is Status.DONE


# ---- Stage 6 — fix-executor failure: verifier FAILED + cascade -------------


def test_fix_executor_failure_fails_verifier_and_cascades() -> None:
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)
    tc.submit_verification_failure(verifier_id, "boom")

    fix = next(
        t
        for t in tc.graph.tasks.values()
        if t.spawn_reason == "fix_verification"
    )
    tc.graph.transition(fix.id, Status.RUNNING)
    tc.submit_task_failure(fix.id, "couldn't repair, scope too wide")

    # Verifier FAILED, descendants cascade.
    assert tc.graph.get(verifier_id).status is Status.FAILED
    assert tc.graph.get(downstream_id).status is Status.FAILED
    assert tc.graph.get(fix.id).status is Status.FAILED


# ---- silent termination on a verifier still routes through Stage 6 ---------


def test_verifier_silent_termination_routes_to_fixing() -> None:
    """Silent verifier exit triggers ``submit_verification_failure`` →
    Stage 6 path (verifier FIXING + fix-executor spawned)."""
    tc = TaskCenter()
    _, verifier_id, downstream_id = _build_two_node_graph(tc)

    verifier = tc.graph.get(verifier_id)
    tc._handle_silent_termination(verifier, "agent crashed")

    assert tc.graph.get(verifier_id).status is Status.FIXING
    # Downstream waits for the fix attempt rather than cascading immediately.
    assert tc.graph.get(downstream_id).status is Status.PENDING
    fix_execs = [
        t
        for t in tc.graph.tasks.values()
        if t.spawn_reason == "fix_verification"
    ]
    assert len(fix_execs) == 1
