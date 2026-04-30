"""TaskSegmentManager lifecycle tests."""

from __future__ import annotations

import pytest

from task_center.segment.manager import TaskSegmentManager
from task_center.harness_graph.graph import (
    HarnessGraphFailReason,
    HarnessGraphStatus,
)
from task_center.segment.closure_report import (
    AttemptPlanFailed,
    SuccessContinue,
    TaskSegmentClosureReport,
    TerminalSuccess,
)
from task_center.segment.segment import (
    TaskSegmentCreationReason,
    TaskSegmentStatus,
)


def _seed_segment(
    request_store, segment_store, task_center_run_id, attempt_budget=2
) -> str:
    req = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    seg = segment_store.insert(
        complex_task_request_id=req.id,
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="g",
        attempt_budget=attempt_budget,
    )
    return seg.id


def _make_manager(seg_id, segment_store, graph_store):
    captured: list[TaskSegmentClosureReport] = []
    mgr = TaskSegmentManager(
        task_segment_id=seg_id,
        segment_store=segment_store,
        graph_store=graph_store,
        on_segment_closed=captured.append,
    )
    return mgr, captured


class _StartedOrchestrator:
    def __init__(self, graph_id: str, started: list[str]) -> None:
        self.harness_graph_id = graph_id
        self._started = started

    def start(self) -> None:
        self._started.append(self.harness_graph_id)


def test_initial_segment_creates_graph_sequence_1(
    request_store, segment_store, graph_store, task_center_run_id
):
    """Phase 01 exit: create segment 1 with harness graph sequence 1."""
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, _ = _make_manager(seg_id, segment_store, graph_store)
    g = mgr.create_initial_harness_graph()
    assert g.graph_sequence_no == 1
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.harness_graph_ids == (g.id,)


def test_retry_creates_graph_in_same_segment(
    request_store, segment_store, graph_store, task_center_run_id
):
    """Phase 01 exit: retry creates another HarnessGraph in the same segment."""
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, _ = _make_manager(seg_id, segment_store, graph_store)
    g1 = mgr.create_initial_harness_graph()
    g2 = mgr.create_next_harness_graph(previous_harness_graph_id=g1.id)
    assert g2.task_segment_id == seg_id
    assert g2.graph_sequence_no == 2
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.harness_graph_ids == (g1.id, g2.id)


def test_passing_graph_with_null_continuation_emits_terminal_success(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, captured = _make_manager(seg_id, segment_store, graph_store)
    g = mgr.create_initial_harness_graph()
    # No continuation_goal set on the graph.
    graph_store.close(
        g.id, status=HarnessGraphStatus.PASSED, fail_reason=None
    )
    mgr.handle_harness_graph_closed(g.id)
    assert len(captured) == 1
    assert isinstance(captured[0].outcome, TerminalSuccess)
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.status == TaskSegmentStatus.SUCCEEDED


def test_passing_graph_with_continuation_emits_success_continue(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, captured = _make_manager(seg_id, segment_store, graph_store)
    g = mgr.create_initial_harness_graph()
    graph_store.set_plan_contract(
        g.id,
        task_specification="spec",
        evaluation_criteria=["c1"],
        continuation_goal="next-goal",
    )
    graph_store.close(
        g.id, status=HarnessGraphStatus.PASSED, fail_reason=None
    )
    mgr.handle_harness_graph_closed(g.id)
    assert len(captured) == 1
    outcome = captured[0].outcome
    assert isinstance(outcome, SuccessContinue)
    assert outcome.goal == "next-goal"
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.continuation_goal == "next-goal"


def test_passing_graph_does_not_retry(
    request_store, segment_store, graph_store, task_center_run_id
):
    """Spec rule: passing graph always closes the segment; no second graph."""
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, _ = _make_manager(seg_id, segment_store, graph_store)
    g = mgr.create_initial_harness_graph()
    graph_store.close(
        g.id, status=HarnessGraphStatus.PASSED, fail_reason=None
    )
    mgr.handle_harness_graph_closed(g.id)
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.harness_graph_ids == (g.id,)
    assert seg.status == TaskSegmentStatus.SUCCEEDED


def test_failed_graph_with_budget_creates_next_graph(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id, attempt_budget=2)
    mgr, captured = _make_manager(seg_id, segment_store, graph_store)
    g1 = mgr.create_initial_harness_graph()
    graph_store.close(
        g1.id,
        status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
    )
    mgr.handle_harness_graph_closed(g1.id)
    assert captured == []  # No closure report yet — segment still open.
    seg = segment_store.get(seg_id)
    assert seg is not None
    assert seg.is_open
    assert len(seg.harness_graph_ids) == 2


def test_manager_starts_orchestrator_when_factory_present(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    started: list[str] = []

    def factory(graph, on_graph_closed):
        del on_graph_closed
        return _StartedOrchestrator(graph.id, started)

    captured: list[TaskSegmentClosureReport] = []
    mgr = TaskSegmentManager(
        task_segment_id=seg_id,
        segment_store=segment_store,
        graph_store=graph_store,
        on_segment_closed=captured.append,
        orchestrator_factory=factory,
    )

    graph = mgr.create_initial_harness_graph()

    assert started == [graph.id]


def test_failed_graph_with_budget_starts_next_graph_orchestrator(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id, attempt_budget=2)
    started: list[str] = []

    def factory(graph, on_graph_closed):
        del on_graph_closed
        return _StartedOrchestrator(graph.id, started)

    captured: list[TaskSegmentClosureReport] = []
    mgr = TaskSegmentManager(
        task_segment_id=seg_id,
        segment_store=segment_store,
        graph_store=graph_store,
        on_segment_closed=captured.append,
        orchestrator_factory=factory,
    )
    graph = mgr.create_initial_harness_graph()
    graph_store.close(
        graph.id,
        status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
    )

    mgr.handle_harness_graph_closed(graph.id)

    segment = segment_store.get(seg_id)
    assert segment is not None
    assert started == list(segment.harness_graph_ids)
    assert captured == []


def test_failed_graph_without_budget_emits_attempt_plan_failed(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id, attempt_budget=2)
    mgr, captured = _make_manager(seg_id, segment_store, graph_store)
    g1 = mgr.create_initial_harness_graph()
    graph_store.set_plan_contract(
        g1.id, task_specification="spec1", evaluation_criteria=["a"], continuation_goal=None
    )
    graph_store.close(
        g1.id,
        status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
    )
    mgr.handle_harness_graph_closed(g1.id)
    # second attempt
    seg = segment_store.get(seg_id)
    assert seg is not None
    g2_id = seg.harness_graph_ids[-1]
    graph_store.set_plan_contract(
        g2_id, task_specification="spec2", evaluation_criteria=["b"], continuation_goal=None
    )
    graph_store.close(
        g2_id,
        status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.EVALUATOR_FAILED,
    )
    mgr.handle_harness_graph_closed(g2_id)
    assert len(captured) == 1
    outcome = captured[0].outcome
    assert isinstance(outcome, AttemptPlanFailed)
    assert outcome.failure_summary == HarnessGraphFailReason.EVALUATOR_FAILED.value


def test_attempted_plan_history_ordered_by_graph_sequence(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id, attempt_budget=2)
    mgr, captured = _make_manager(seg_id, segment_store, graph_store)
    g1 = mgr.create_initial_harness_graph()
    graph_store.set_plan_contract(
        g1.id, task_specification="spec1", evaluation_criteria=["a"], continuation_goal=None
    )
    graph_store.close(
        g1.id, status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
    )
    mgr.handle_harness_graph_closed(g1.id)
    seg = segment_store.get(seg_id)
    assert seg is not None
    g2_id = seg.harness_graph_ids[-1]
    graph_store.set_plan_contract(
        g2_id, task_specification="spec2", evaluation_criteria=["b"], continuation_goal=None
    )
    graph_store.close(
        g2_id, status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.EVALUATOR_FAILED,
    )
    mgr.handle_harness_graph_closed(g2_id)
    outcome = captured[0].outcome
    assert isinstance(outcome, AttemptPlanFailed)
    seqs = [e.graph_sequence_no for e in outcome.attempted_plan_history]
    assert seqs == [1, 2]
    assert outcome.attempted_plan_history[0].harness_graph_summary_id is None
    assert outcome.attempted_plan_history[0].failure_landscape is None


def test_get_attempt_count_derived_from_list(
    request_store, segment_store, graph_store, task_center_run_id
):
    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, _ = _make_manager(seg_id, segment_store, graph_store)
    assert mgr.get_attempt_count() == 0
    g1 = mgr.create_initial_harness_graph()
    assert mgr.get_attempt_count() == 1
    graph_store.close(
        g1.id, status=HarnessGraphStatus.FAILED,
        fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
    )
    mgr.create_next_harness_graph(previous_harness_graph_id=g1.id)
    assert mgr.get_attempt_count() == 2


def test_creating_initial_graph_twice_raises(
    request_store, segment_store, graph_store, task_center_run_id
):
    from task_center.exceptions import GraphInvariantViolation

    seg_id = _seed_segment(request_store, segment_store, task_center_run_id)
    mgr, _ = _make_manager(seg_id, segment_store, graph_store)
    mgr.create_initial_harness_graph()
    with pytest.raises(GraphInvariantViolation):
        mgr.create_initial_harness_graph()
