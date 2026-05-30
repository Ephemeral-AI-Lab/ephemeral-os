"""Tests for IterationClosureReport variants and history shape."""

from __future__ import annotations

from task_center.iteration.state import (
    AttemptPlanFailed,
    SuccessDeferred,
    IterationClosureReport,
    TerminalSuccess,
)


def test_terminal_success_constructs():
    o = TerminalSuccess()
    assert o.kind == "terminal_success"


def test_success_deferred_carries_deferred_goal():
    o = SuccessDeferred(deferred_goal_for_next_iteration="next")
    assert o.kind == "success_deferred"
    assert o.deferred_goal_for_next_iteration == "next"


def test_attempt_plan_failed_constructs():
    o = AttemptPlanFailed()
    assert o.kind == "attempt_plan_failed"


def test_closure_report_carries_outcome():
    rep = IterationClosureReport(
        iteration_id="s1",
        final_attempt_id="g1",
        outcome=TerminalSuccess(),
    )
    assert rep.outcome.kind == "terminal_success"
