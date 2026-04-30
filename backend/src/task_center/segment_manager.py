"""TaskSegmentManager — per-segment retry and closure-report emitter.

Sole creator of HarnessGraph records inside its owned segment, and the only
emitter of ``TaskSegmentClosureReport``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from db.stores.harness_graph_store import HarnessGraphStore
from db.stores.task_segment_store import TaskSegmentStore
from task_center.invariants import (
    assert_continuation_goal_only_from_passing_graph,
    assert_fail_reason_present_on_failure,
    assert_graph_belongs_to_segment,
    assert_graph_sequence_contiguous,
    assert_passing_graph_closes_segment,
    assert_segment_has_budget,
    assert_segment_open,
)
from task_center.domain.harness_graph import HarnessGraph, HarnessGraphStatus
from task_center.domain.segment_closure_report import (
    AttemptedPlanEntry,
    AttemptPlanFailed,
    SuccessContinue,
    TaskSegmentClosureReport,
    TerminalSuccess,
)
from task_center.domain.task_segment import TaskSegment, TaskSegmentStatus
from task_center.exceptions import GraphInvariantViolation

if TYPE_CHECKING:
    from task_center.graph_orchestrator import HarnessGraphOrchestrator


OrchestratorFactory = Callable[[HarnessGraph], "HarnessGraphOrchestrator"]
ClosureReportSink = Callable[[TaskSegmentClosureReport], None]


class TaskSegmentManager:
    """Manages one open TaskSegment's lifecycle."""

    def __init__(
        self,
        *,
        task_segment_id: str,
        segment_store: TaskSegmentStore,
        graph_store: HarnessGraphStore,
        on_segment_closed: ClosureReportSink,
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self.task_segment_id = task_segment_id
        self._segment_store = segment_store
        self._graph_store = graph_store
        self._on_segment_closed = on_segment_closed
        self._orchestrator_factory = orchestrator_factory

    # ---- public API -----------------------------------------------------

    def create_initial_harness_graph(self) -> HarnessGraph:
        """Create graph_sequence_no=1 and append it to the segment."""
        segment = self._current_segment_snapshot()
        assert_segment_open(segment)
        if segment.harness_graph_ids:
            raise GraphInvariantViolation(
                f"TaskSegment {segment.id!r} already has graphs; use "
                f"create_next_harness_graph"
            )
        return self._create_graph(segment, graph_sequence_no=1)

    def create_next_harness_graph(
        self, *, previous_harness_graph_id: str
    ) -> HarnessGraph:
        """Called after a failed graph if the segment still has budget."""
        segment = self._current_segment_snapshot()
        assert_segment_open(segment)
        assert_segment_has_budget(segment)
        if segment.latest_graph_id != previous_harness_graph_id:
            raise GraphInvariantViolation(
                f"previous_harness_graph_id {previous_harness_graph_id!r} is not "
                f"the latest graph of segment {segment.id!r} "
                f"(latest={segment.latest_graph_id!r})"
            )
        return self._create_graph(
            segment, graph_sequence_no=segment.attempt_count + 1
        )

    def handle_harness_graph_closed(self, harness_graph_id: str) -> None:
        """Entry point for the closed-graph callback from the orchestrator."""
        graph = self._graph_store.get(harness_graph_id)
        if graph is None:
            raise GraphInvariantViolation(
                f"HarnessGraph {harness_graph_id!r} not found"
            )
        segment = self._current_segment_snapshot()
        assert_segment_open(segment)
        assert_graph_belongs_to_segment(graph, segment)
        assert_fail_reason_present_on_failure(graph)

        if (
            graph.has_partial_continuation
            and graph.status != HarnessGraphStatus.PASSED
        ):
            raise GraphInvariantViolation(
                f"HarnessGraph {graph.id!r} has continuation_goal but did not "
                f"pass (status={graph.status})"
            )
        if graph.status == HarnessGraphStatus.PASSED:
            self._close_segment_passed(graph)
        else:
            self._retry_or_close_failed(graph)

    def get_attempt_count(self) -> int:
        return self._current_segment_snapshot().attempt_count

    # ---- internal -------------------------------------------------------

    def _current_segment_snapshot(self) -> TaskSegment:
        segment = self._segment_store.get(self.task_segment_id)
        if segment is None:
            raise GraphInvariantViolation(
                f"TaskSegment {self.task_segment_id!r} not found"
            )
        return segment

    def _create_graph(
        self, segment: TaskSegment, *, graph_sequence_no: int
    ) -> HarnessGraph:
        assert_graph_sequence_contiguous(segment, graph_sequence_no)
        graph = self._graph_store.insert(
            task_segment_id=segment.id,
            graph_sequence_no=graph_sequence_no,
        )
        self._segment_store.append_graph_id(segment.id, graph.id)
        return graph

    def _close_segment_passed(self, graph: HarnessGraph) -> None:
        assert_passing_graph_closes_segment(graph)
        segment = self._segment_store.set_continuation_goal(
            self.task_segment_id, graph.continuation_goal
        )
        assert_continuation_goal_only_from_passing_graph(graph, segment)
        self._segment_store.set_status(
            self.task_segment_id,
            status=TaskSegmentStatus.SUCCEEDED,
            closed_at=datetime.now(UTC),
        )
        if graph.continuation_goal is None:
            self._emit_terminal_success(graph)
        else:
            self._emit_success_continue(graph)

    def _retry_or_close_failed(self, graph: HarnessGraph) -> None:
        segment = self._current_segment_snapshot()
        if segment.has_budget_remaining:
            self.create_next_harness_graph(previous_harness_graph_id=graph.id)
            return
        self._segment_store.set_status(
            self.task_segment_id,
            status=TaskSegmentStatus.FAILED,
            closed_at=datetime.now(UTC),
        )
        self._emit_attempt_plan_failed(graph)

    def _emit_terminal_success(self, graph: HarnessGraph) -> None:
        report = TaskSegmentClosureReport(
            task_segment_id=self.task_segment_id,
            final_harness_graph_id=graph.id,
            outcome=TerminalSuccess(),
        )
        self._on_segment_closed(report)

    def _emit_success_continue(self, graph: HarnessGraph) -> None:
        if graph.continuation_goal is None:
            raise GraphInvariantViolation(
                "success_continue requires a non-null continuation_goal"
            )
        report = TaskSegmentClosureReport(
            task_segment_id=self.task_segment_id,
            final_harness_graph_id=graph.id,
            outcome=SuccessContinue(goal=graph.continuation_goal),
        )
        self._on_segment_closed(report)

    def _emit_attempt_plan_failed(self, last_graph: HarnessGraph) -> None:
        history = self._build_attempted_plan_history()
        report = TaskSegmentClosureReport(
            task_segment_id=self.task_segment_id,
            final_harness_graph_id=last_graph.id,
            outcome=AttemptPlanFailed(
                failure_summary=(
                    last_graph.fail_reason.value
                    if last_graph.fail_reason is not None
                    else "unknown"
                ),
                attempted_plan_history=history,
            ),
        )
        self._on_segment_closed(report)

    def _build_attempted_plan_history(self) -> tuple[AttemptedPlanEntry, ...]:
        graphs = self._graph_store.list_for_segment(self.task_segment_id)
        return tuple(
            AttemptedPlanEntry(
                harness_graph_id=g.id,
                graph_sequence_no=g.graph_sequence_no,
                task_specification=g.task_specification,
                evaluation_criteria=g.evaluation_criteria,
                fail_reason=g.fail_reason,
                harness_graph_summary_id=None,
                failure_landscape=None,
            )
            for g in graphs
        )
