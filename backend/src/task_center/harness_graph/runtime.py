"""Runtime dependency seam for harness graph orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from db.stores.complex_task_request_store import ComplexTaskRequestStore
from db.stores.task_center_store import TaskCenterStore
from db.stores.task_segment_store import TaskSegmentStore
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.graph import HarnessGraph
from task_center.harness_graph.task import HarnessTaskRole

if TYPE_CHECKING:
    from task_center.harness_graph.orchestrator_registry import (
        HarnessGraphOrchestratorRegistry,
    )


@dataclass(frozen=True, slots=True)
class HarnessAgentLaunch:
    task_id: str
    task_center_run_id: str
    harness_graph_id: str
    role: HarnessTaskRole
    agent_name: str
    task_input: str
    needs: tuple[str, ...]


class HarnessAgentLauncher(Protocol):
    """Launches or queues one harness agent task."""

    def launch(self, launch: HarnessAgentLaunch) -> None: ...


@dataclass(frozen=True, slots=True)
class HarnessGraphRuntime:
    request_store: ComplexTaskRequestStore
    segment_store: TaskSegmentStore
    task_store: TaskCenterStore
    agent_launcher: HarnessAgentLauncher
    orchestrator_registry: "HarnessGraphOrchestratorRegistry"

    def task_center_run_id_for_graph(self, graph: HarnessGraph) -> str:
        segment = self.segment_store.get(graph.task_segment_id)
        if segment is None:
            raise GraphInvariantViolation(
                f"TaskSegment {graph.task_segment_id!r} not found for "
                f"HarnessGraph {graph.id!r}"
            )
        request = self.request_store.get(segment.complex_task_request_id)
        if request is None:
            raise GraphInvariantViolation(
                f"ComplexTaskRequest {segment.complex_task_request_id!r} not "
                f"found for TaskSegment {segment.id!r}"
            )
        return request.task_center_run_id

    def task_input_for_graph(self, graph: HarnessGraph) -> str:
        segment = self.segment_store.get(graph.task_segment_id)
        if segment is None:
            raise GraphInvariantViolation(
                f"TaskSegment {graph.task_segment_id!r} not found for "
                f"HarnessGraph {graph.id!r}"
            )
        return segment.goal
