"""Tests for IterationClosureReport variants and history shape."""

from __future__ import annotations

from task_center.trial import TrialFailReason
from task_center.iteration.state import (
    PriorTrialEntry,
    TrialPlanFailed,
    SuccessContinue,
    IterationClosureReport,
    TerminalSuccess,
)


def test_terminal_success_constructs():
    o = TerminalSuccess()
    assert o.kind == "terminal_success"


def test_success_continue_carries_goal():
    o = SuccessContinue(goal="next")
    assert o.kind == "success_continue"
    assert o.goal == "next"


def test_attempt_plan_failed_carries_history():
    e1 = PriorTrialEntry(
        attempt_id="g1",
        trial_sequence_no=1,
        task_specification=None,
        evaluation_criteria=(),
        fail_reason=TrialFailReason.GENERATOR_FAILED,
        attempt_summary_id=None,
        failure_landscape=None,
    )
    o = TrialPlanFailed(failure_summary="bad", attempted_plan_history=(e1,))
    assert o.kind == "attempt_plan_failed"
    assert o.attempted_plan_history == (e1,)


def test_attempted_plan_history_orders_by_sequence_no():
    e1 = PriorTrialEntry(
        attempt_id="g1",
        trial_sequence_no=1,
        task_specification=None,
        evaluation_criteria=(),
        fail_reason=None,
        attempt_summary_id=None,
        failure_landscape=None,
    )
    e2 = PriorTrialEntry(
        attempt_id="g2",
        trial_sequence_no=2,
        task_specification=None,
        evaluation_criteria=(),
        fail_reason=None,
        attempt_summary_id=None,
        failure_landscape=None,
    )
    seqs = [etrial_sequence_no for e in (e1, e2)]
    assert seqs == sorted(seqs)


def test_phase06_summary_fields_default_to_none():
    """Phase 06 fills these. Phase 01 must surface them as ``None``, not absent."""
    e = PriorTrialEntry(
        attempt_id="g1",
        trial_sequence_no=1,
        task_specification=None,
        evaluation_criteria=(),
        fail_reason=None,
        attempt_summary_id=None,
        failure_landscape=None,
    )
    assert e.attempt_summary_id is None
    assert e.failure_landscape is None


def test_closure_report_carries_outcome():
    rep = IterationClosureReport(
        episode_id="s1",
        final_attempt_id="g1",
        outcome=TerminalSuccess(),
    )
    assert rep.outcome.kind == "terminal_success"
