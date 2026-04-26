"""TaskGraph — in-memory container for tasks and harness graphs.

Holds the ``{task_id: Task}`` and ``{graph_id: HarnessGraph}`` maps plus the
orchestrator-facing operations: insertion, lookup, readiness, and status
transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from task_center.errors import TaskCenterError
from task_center.model import HarnessGraph, HarnessGraphId, Status, Task, TaskId


_ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.READY, Status.FAILED},
    Status.READY: {Status.RUNNING, Status.FAILED},
    Status.RUNNING: {Status.HANDOFF, Status.DONE, Status.FAILED},
    Status.HANDOFF: {Status.DONE, Status.FAILED},
    Status.DONE: set(),
    Status.FAILED: set(),
}


@dataclass
class TaskGraph:
    """Request-scoped tasks plus harness graphs."""

    tasks: dict[TaskId, Task] = field(default_factory=dict)
    harness_graphs: dict[HarnessGraphId, HarnessGraph] = field(default_factory=dict)

    def add(self, task: Task) -> None:
        if task.id in self.tasks:
            raise TaskCenterError(f"task id {task.id!r} already in graph")
        self.tasks[task.id] = task

    def get(self, task_id: TaskId) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskCenterError(f"task id {task_id!r} not in graph")
        return task

    def add_harness_graph(self, graph: HarnessGraph) -> None:
        if graph.id in self.harness_graphs:
            raise TaskCenterError(f"harness graph id {graph.id!r} already in graph")
        self.harness_graphs[graph.id] = graph

    def get_harness_graph(self, graph_id: HarnessGraphId) -> HarnessGraph:
        graph = self.harness_graphs.get(graph_id)
        if graph is None:
            raise TaskCenterError(f"harness graph id {graph_id!r} not in graph")
        return graph

    def ready_tasks(self) -> list[Task]:
        """Return tasks eligible for dispatch.

        A READY task is dispatched. A PENDING task is promoted to READY when
        every direct dependency in ``needs`` is DONE (or absent — needs can
        only reference tasks in the same harness graph that are present).
        """
        out: list[Task] = []
        for task in self.tasks.values():
            if task.status is Status.READY:
                out.append(task)
            elif task.status is Status.PENDING and all(
                self.tasks.get(dep) is not None
                and self.tasks[dep].status is Status.DONE
                for dep in task.needs
            ):
                out.append(task)
        return out

    def transition(self, task_id: TaskId, new_status: Status) -> None:
        task = self.get(task_id)
        allowed = _ALLOWED_TRANSITIONS[task.status]
        if new_status not in allowed:
            raise ValueError(
                f"illegal transition {task.status.value!r} -> "
                f"{new_status.value!r} for task {task_id!r}"
            )
        task.status = new_status
