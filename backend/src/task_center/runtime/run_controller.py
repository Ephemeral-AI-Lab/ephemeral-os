"""RunController — run-level coordinator that owns the root executor.

The root executor is the one task in a run that lives outside any
:class:`HarnessGraph`. After ``RunController.start`` creates it, every
capability of the root executor (calling ``request_plan``, terminating with
success/failure) routes through the same dispatcher as any in-graph executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.model import Status, Task, TaskId

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


@dataclass
class RunController:
    """Owns ``root_exec``: status checks, terminal detection, single source of truth."""

    tc: "TaskCenter"
    root_task_id: TaskId | None = None

    def start(self, prompt: str) -> Task:
        """Create the root executor via :meth:`TaskCenter._create_executor`.

        ``status=READY``, ``harness_graph_id=None``, ``needs=frozenset()``,
        ``input=prompt``. The dispatcher picks the task up on its next tick.
        """
        task = self.tc._create_executor(
            input=prompt,
            harness_graph_id=None,
            needs=frozenset(),
            status=Status.READY,
        )
        self.root_task_id = task.id
        if (
            self.tc._task_center_store is not None
            and self.tc.run_id is not None
        ):
            self.tc._task_center_store.set_run_root(
                self.tc.run_id, self.tc.persisted_task_id(task.id)
            )
        self.tc._persist_task(task)
        return task

    @property
    def root_task(self) -> Task:
        if self.root_task_id is None:
            raise RuntimeError("RunController.start has not been called yet")
        return self.tc.graph.get(self.root_task_id)

    def is_done(self) -> bool:
        return self.root_task.status in (Status.DONE, Status.FAILED)


__all__ = ["RunController"]
