"""TaskGraph — in-memory container for the per-session task tree.

Holds the ``{task_id: Task}`` map and exposes the orchestrator-facing
operations: insertion, lookup, children traversal, readiness check,
status transitions, and final-phase-passed check for evaluator launch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from task_center.errors import TaskCenterError
from task_center.task import Status, Task, TaskId


# Allowed status transitions. AWAITING -> DONE is intentionally absent
# (invariant 14: AWAITING can only close via summary propagation, which
# bypasses transition() and writes status directly).
_ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.READY, Status.FAILED},
    Status.READY: {Status.RUNNING, Status.FAILED},
    Status.RUNNING: {Status.AWAITING, Status.DONE, Status.FAILED},
    Status.AWAITING: {Status.FAILED},
    Status.DONE: set(),
    Status.FAILED: set(),
}


@dataclass
class TaskGraph:
    """Per-session task graph."""

    tasks: dict[TaskId, Task] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ------------------------------------------------------------------ #
    # Insertion / lookup                                                 #
    # ------------------------------------------------------------------ #

    def add(self, task: Task) -> None:
        if task.id in self.tasks:
            raise TaskCenterError(f"task id {task.id!r} already in graph")
        self.tasks[task.id] = task

    def get(self, task_id: TaskId) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskCenterError(f"task id {task_id!r} not in graph")
        return task

    def children_of(self, task_id: TaskId) -> list[Task]:
        parent = self.get(task_id)
        return [self.tasks[cid] for cid in parent.children if cid in self.tasks]

    # ------------------------------------------------------------------ #
    # Readiness                                                          #
    # ------------------------------------------------------------------ #

    def ready_tasks(self) -> list[Task]:
        """Tasks eligible to be picked up by the dispatcher.

        Returns tasks where status is :attr:`Status.READY`, OR status is
        :attr:`Status.PENDING` with every ``needs`` id present in the graph
        and at status :attr:`Status.DONE`. The dispatcher promotes the
        latter (PENDING -> READY) before launching them.
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

    # ------------------------------------------------------------------ #
    # Status transitions                                                 #
    # ------------------------------------------------------------------ #

    def transition(self, task_id: TaskId, new_status: Status) -> None:
        """Move ``task_id`` to ``new_status`` if the move is allowed."""
        task = self.get(task_id)
        allowed = _ALLOWED_TRANSITIONS[task.status]
        if new_status not in allowed:
            raise ValueError(
                f"illegal transition {task.status.value!r} -> "
                f"{new_status.value!r} for task {task_id!r}"
            )
        task.status = new_status

    # ------------------------------------------------------------------ #
    # Evaluator launch gate                                              #
    # ------------------------------------------------------------------ #

    def all_final_phase_passed(self, parent_executor_id: TaskId) -> bool:
        """True iff every direct child of ``parent_executor_id`` whose phase
        equals the maximum phase has status DONE.
        """
        children = self.children_of(parent_executor_id)
        if not children:
            return False
        phased = [c for c in children if c.phase is not None]
        if not phased:
            return False
        max_phase = max(c.phase for c in phased)  # type: ignore[type-var]
        final_phase_children = [c for c in phased if c.phase == max_phase]
        return all(c.status is Status.DONE for c in final_phase_children)
