"""ComplexTaskHandoffCoordinator — use-case boundary for delegated request start.

Composes the existing request, segment, manager, and parent-task owners into
the single safe handoff path used by ``request_complex_task_solution``. Owns
parent-task CAS, deferred orchestrator startup, and compensation on failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from task_center.complex_task.close_report_delivery import (
    ComplexTaskCloseReportRouter,
)
from task_center.complex_task.handler import ComplexTaskRequestHandler
from task_center.complex_task.request import (
    ComplexTaskCloseReport,
    ComplexTaskRequest,
)
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.factory import (
    make_harness_graph_orchestrator_factory,
)
from task_center.harness_graph.runtime import HarnessGraphRuntime
from task_center.segment.manager import HarnessGraphStartHandle
from task_center.segment.segment import TaskSegment
from task_center.task import HarnessTaskStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ComplexTaskHandoffResult:
    parent_task_id: str
    parent_harness_graph_id: str
    complex_task_request_id: str
    initial_segment_id: str
    initial_harness_graph_id: str
    goal: str


class ComplexTaskHandoffCoordinator:
    """Single orchestration entry point for executor → delegated request handoff."""

    def __init__(self, *, runtime: HarnessGraphRuntime) -> None:
        self._runtime = runtime

    def start(
        self,
        *,
        task_center_run_id: str,
        parent_task_id: str,
        parent_harness_graph_id: str,
        goal: str,
    ) -> ComplexTaskHandoffResult:
        self._assert_parent_running_and_no_open_child(
            parent_task_id=parent_task_id,
            parent_harness_graph_id=parent_harness_graph_id,
        )

        handler = self._build_handler()
        delegated_request = handler.create_complex_task_request(
            task_center_run_id=task_center_run_id,
            requested_by_task_id=parent_task_id,
            goal=goal,
        )
        initial_segment = handler.create_initial_segment(
            complex_task_request_id=delegated_request.id,
        )
        # _build_handler() above already proved manager_registry is not None.
        manager_registry = self._runtime.manager_registry
        assert manager_registry is not None
        segment_manager = manager_registry.get(initial_segment.id)
        if segment_manager is None:
            raise GraphInvariantViolation(
                f"TaskSegmentManager {initial_segment.id!r} was not registered."
            )

        start_handle: HarnessGraphStartHandle | None = None
        try:
            start_handle = segment_manager.create_initial_harness_graph()
            self._mark_parent_waiting(
                parent_task_id=parent_task_id,
                parent_harness_graph_id=parent_harness_graph_id,
                request=delegated_request,
                segment=initial_segment,
                graph_id=start_handle.graph.id,
                goal=goal,
            )
            start_handle.start()
        except Exception:
            self._compensate_failed_handoff(
                request=delegated_request,
                segment=initial_segment,
                start_handle=start_handle,
                parent_task_id=parent_task_id,
            )
            raise

        return ComplexTaskHandoffResult(
            parent_task_id=parent_task_id,
            parent_harness_graph_id=parent_harness_graph_id,
            complex_task_request_id=delegated_request.id,
            initial_segment_id=initial_segment.id,
            initial_harness_graph_id=start_handle.graph.id,
            goal=goal,
        )

    # ---- internal -------------------------------------------------------

    def _build_handler(self) -> ComplexTaskRequestHandler:
        manager_registry = self._runtime.manager_registry
        if manager_registry is None:
            raise GraphInvariantViolation(
                "ComplexTaskHandoffCoordinator requires a segment manager registry."
            )
        router = ComplexTaskCloseReportRouter(runtime=self._runtime)

        def _deliver(report: ComplexTaskCloseReport) -> None:
            router.deliver(report)

        orchestrator_factory = make_harness_graph_orchestrator_factory(
            graph_store=self._runtime.graph_store,
            runtime=self._runtime,
        )
        return ComplexTaskRequestHandler(
            request_store=self._runtime.request_store,
            segment_store=self._runtime.segment_store,
            graph_store=self._runtime.graph_store,
            manager_registry=manager_registry,
            config=self._runtime.lifecycle_config,
            deliver_close_report=_deliver,
            orchestrator_factory=orchestrator_factory,
        )

    def _assert_parent_running_and_no_open_child(
        self,
        *,
        parent_task_id: str,
        parent_harness_graph_id: str,
    ) -> None:
        task = self._runtime.task_store.get_task(parent_task_id)
        if task is None:
            raise GraphInvariantViolation(
                f"TaskCenter task {parent_task_id!r} was not found."
            )
        if task.get("status") != HarnessTaskStatus.RUNNING.value:
            raise GraphInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is not running; "
                "complex-task handoff requires a running generator task."
            )
        attached_graph = str(task.get("task_center_harness_graph_id") or "")
        if attached_graph != parent_harness_graph_id:
            raise GraphInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is attached to graph "
                f"{attached_graph!r}, not {parent_harness_graph_id!r}."
            )
        existing_open = [
            r
            for r in self._runtime.request_store.list_for_executor_task(
                parent_task_id
            )
            if r.is_open
        ]
        if existing_open:
            raise GraphInvariantViolation(
                f"TaskCenter task {parent_task_id!r} already has an open "
                f"complex-task request {existing_open[0].id!r}."
            )

    def _mark_parent_waiting(
        self,
        *,
        parent_task_id: str,
        parent_harness_graph_id: str,
        request: ComplexTaskRequest,
        segment: TaskSegment,
        graph_id: str,
        goal: str,
    ) -> None:
        summary = {
            "outcome": "complex_task_request_start",
            "summary": "Waiting on delegated complex task solution.",
            "payload": {
                "complex_task_request_id": request.id,
                "initial_segment_id": segment.id,
                "initial_harness_graph_id": graph_id,
                "parent_harness_graph_id": parent_harness_graph_id,
                "goal": goal,
            },
        }
        updated = self._runtime.task_store.set_task_status_if_current(
            parent_task_id,
            expected_status=HarnessTaskStatus.RUNNING.value,
            status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
            summary=summary,
        )
        if updated is None:
            raise GraphInvariantViolation(
                f"TaskCenter task {parent_task_id!r} was not running when the "
                "complex-task handoff tried to mark it waiting."
            )

    def _compensate_failed_handoff(
        self,
        *,
        request: ComplexTaskRequest,
        segment: TaskSegment,
        start_handle: HarnessGraphStartHandle | None,
        parent_task_id: str,
    ) -> None:
        """Best-effort rollback. Order: handle → segment → request → parent."""
        now = datetime.now(UTC)
        if start_handle is not None:
            try:
                start_handle.cancel()
            except GraphInvariantViolation:
                # Handle was already started — segment and request still need
                # cancelling; the orchestrator/registry will be cleaned up
                # when the failure path closes the graph.
                pass
            except Exception:
                logger.exception(
                    "ComplexTaskHandoffCoordinator: cancel start handle failed",
                )
        try:
            self._runtime.segment_store.cancel_for_compensation(
                segment.id, closed_at=now
            )
        except Exception:
            logger.exception(
                "ComplexTaskHandoffCoordinator: cancel segment failed",
            )
        try:
            self._runtime.request_store.cancel_for_compensation(
                request.id, closed_at=now
            )
        except Exception:
            logger.exception(
                "ComplexTaskHandoffCoordinator: cancel request failed",
            )
        try:
            self._runtime.task_store.set_task_status_if_current(
                parent_task_id,
                expected_status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
                status=HarnessTaskStatus.RUNNING.value,
            )
        except Exception:
            logger.exception(
                "ComplexTaskHandoffCoordinator: rollback parent status failed",
            )
        manager_registry = self._runtime.manager_registry
        if manager_registry is not None:
            manager_registry.deregister(segment.id)
