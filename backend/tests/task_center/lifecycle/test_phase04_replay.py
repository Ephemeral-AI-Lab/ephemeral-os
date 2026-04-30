"""Phase 04 close-report durable replay tests."""

from __future__ import annotations

from task_center.complex_task.close_report_delivery import (
    build_close_report_from_request,
    deliver_pending_complex_task_close_reports,
)
from task_center.complex_task.request import ComplexTaskRequestStatus
from task_center.harness_graph.orchestrator import HarnessGraphOrchestrator
from task_center.harness_graph.orchestrator_registry import (
    HarnessGraphOrchestratorRegistry,
)
from task_center.harness_graph.runtime import HarnessAgentLaunch, HarnessGraphRuntime
from task_center.segment.registry import SegmentManagerRegistry
from task_center.segment.segment import TaskSegmentCreationReason
from task_center.task import (
    HarnessTaskStatus,
    PlannedGeneratorTask,
    PlannerSubmission,
    generator_task_id,
)


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[HarnessAgentLaunch] = []

    def launch(self, launch: HarnessAgentLaunch) -> None:
        self.launches.append(launch)


def _build_parent_environment(
    *, request_store, segment_store, graph_store, task_store, task_center_run_id: str
):
    """Set up an open parent graph with a generator task currently waiting."""
    request = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="root",
        goal="outer goal",
    )
    segment = segment_store.insert(
        complex_task_request_id=request.id,
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="outer goal",
        attempt_budget=2,
    )
    request_store.append_segment_id(request.id, segment.id)
    graph = graph_store.insert(task_segment_id=segment.id, graph_sequence_no=1)
    segment_store.append_graph_id(segment.id, graph.id)
    registry = HarnessGraphOrchestratorRegistry()
    runtime = HarnessGraphRuntime(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        agent_launcher=_FakeLauncher(),
        orchestrator_registry=registry,
        manager_registry=SegmentManagerRegistry(),
    )
    orchestrator = HarnessGraphOrchestrator(
        harness_graph=graph,
        graph_store=graph_store,
        on_graph_closed=lambda graph_id: None,
        runtime=runtime,
    )
    registry.register(orchestrator)
    orchestrator.start()
    orchestrator.apply_plan_submission(
        PlannerSubmission(
            graph_id=graph.id,
            planner_task_id=f"{graph.id}:planner",
            kind="full",
            task_specification="spec",
            evaluation_criteria=("c",),
            tasks=(
                PlannedGeneratorTask("a", "executor", (), "do A"),
            ),
            continuation_goal=None,
            summary="plan",
        )
    )
    parent_task_id = generator_task_id(graph.id, "a")
    return runtime, orchestrator, graph.id, parent_task_id


def _seed_closed_delegated_request(
    *, request_store, requested_by_task_id: str, task_center_run_id: str
):
    """Insert a closed delegated request with a final_outcome payload."""
    delegated = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id=requested_by_task_id,
        goal="delegated goal",
    )
    request_store.set_status(
        delegated.id,
        status=ComplexTaskRequestStatus.SUCCEEDED,
        final_outcome={
            "outcome": "success",
            "final_segment_id": "final-segment-id",
            "final_harness_graph_id": "final-graph-id",
        },
    )
    return request_store.get(delegated.id)


def test_replay_delivers_closed_request_to_waiting_parent(
    request_store, segment_store, graph_store, task_store, task_center_run_id
) -> None:
    runtime, _, _, parent_task_id = _build_parent_environment(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )
    _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
    )

    results = deliver_pending_complex_task_close_reports(
        runtime=runtime, task_center_run_id=task_center_run_id
    )

    assert any(r.status == "delivered" for r in results)
    parent = task_store.get_task(parent_task_id)
    assert parent is not None
    assert parent["status"] == HarnessTaskStatus.DONE.value


def test_graph_start_replays_closed_request_to_active_waiting_parent(
    request_store, segment_store, graph_store, task_store, task_center_run_id
) -> None:
    runtime, _, _, parent_task_id = _build_parent_environment(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )
    _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
    )
    request = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="root",
        goal="another graph",
    )
    segment = segment_store.insert(
        complex_task_request_id=request.id,
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="another graph",
        attempt_budget=2,
    )
    request_store.append_segment_id(request.id, segment.id)
    graph = graph_store.insert(task_segment_id=segment.id, graph_sequence_no=1)
    segment_store.append_graph_id(segment.id, graph.id)
    orchestrator = HarnessGraphOrchestrator(
        harness_graph=graph,
        graph_store=graph_store,
        on_graph_closed=lambda graph_id: None,
        runtime=runtime,
    )

    orchestrator.start()

    parent = task_store.get_task(parent_task_id)
    assert parent is not None
    assert parent["status"] == HarnessTaskStatus.DONE.value


def test_replay_is_idempotent_after_delivery(
    request_store, segment_store, graph_store, task_store, task_center_run_id
) -> None:
    runtime, _, _, parent_task_id = _build_parent_environment(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )
    _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
    )

    deliver_pending_complex_task_close_reports(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    parent_after_first = task_store.get_task(parent_task_id)
    assert parent_after_first is not None
    summary_count = len(parent_after_first["summaries"])

    second = deliver_pending_complex_task_close_reports(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    parent_after_second = task_store.get_task(parent_task_id)
    assert parent_after_second is not None
    assert len(parent_after_second["summaries"]) == summary_count
    assert all(r.status == "already_delivered" for r in second)


def test_replay_defers_without_parent_orchestrator(
    request_store, segment_store, graph_store, task_store, task_center_run_id
) -> None:
    runtime, _, parent_graph_id, parent_task_id = _build_parent_environment(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )
    _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
    )
    runtime.orchestrator_registry.deregister(parent_graph_id)

    results = deliver_pending_complex_task_close_reports(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    parent = task_store.get_task(parent_task_id)

    assert any(r.status == "deferred_no_orchestrator" for r in results)
    assert parent is not None
    assert parent["status"] == HarnessTaskStatus.WAITING_COMPLEX_TASK.value


def test_build_close_report_from_open_request_returns_none(
    request_store, task_center_run_id
) -> None:
    open_request = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="x",
        goal="g",
    )
    assert build_close_report_from_request(open_request) is None


def test_replay_skips_cancelled_compensation_requests(
    request_store, segment_store, graph_store, task_store, task_center_run_id
) -> None:
    """A request cancelled by handoff compensation has no final_outcome.

    Replay must skip it silently rather than raising on the missing payload.
    Mixing it with a succeeded request makes sure the succeeded one still
    delivers.
    """
    runtime, _, _, parent_task_id = _build_parent_environment(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )
    # Compensation-cancelled request: no final_outcome.
    cancelled = request_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id=parent_task_id,
        goal="cancelled goal",
    )
    request_store.cancel_for_compensation(cancelled.id)
    # Succeeded request that should still be delivered.
    _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
    )

    results = deliver_pending_complex_task_close_reports(
        runtime=runtime, task_center_run_id=task_center_run_id
    )

    assert any(r.status == "delivered" for r in results)
    parent = task_store.get_task(parent_task_id)
    assert parent is not None
    assert parent["status"] == HarnessTaskStatus.DONE.value


def test_build_close_report_from_closed_request_reconstructs_payload(
    request_store, task_center_run_id
) -> None:
    delegated = _seed_closed_delegated_request(
        request_store=request_store,
        requested_by_task_id="executor-1",
        task_center_run_id=task_center_run_id,
    )
    assert delegated is not None
    report = build_close_report_from_request(delegated)
    assert report is not None
    assert report.outcome == "success"
    assert report.final_segment_id == "final-segment-id"
    assert report.final_harness_graph_id == "final-graph-id"
    assert report.requested_by_task_id == "executor-1"
