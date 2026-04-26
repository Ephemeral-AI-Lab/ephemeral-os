"""HarnessGraph — the planner/executor/evaluator decomposition unit."""

from __future__ import annotations

from dataclasses import dataclass, field

from task_center.model.task import HarnessGraphId, TaskId


@dataclass
class HarnessGraph:
    """One planner-led decomposition: planner + executor children + evaluator.

    The graph's ``parent_task_id`` points at the executor or evaluator that
    launched the planner via ``launch_plan_handoff``. The root executor is
    not inside any harness graph.
    """

    id: HarnessGraphId
    run_id: str
    parent_task_id: TaskId
    planner_task_id: TaskId
    evaluator_task_id: TaskId | None = None
    executor_task_ids: list[TaskId] = field(default_factory=list)
