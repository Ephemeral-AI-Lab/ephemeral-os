"""HarnessGraph — the planner/executor/verifier decomposition unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from task_center.model.task import HarnessGraphId, TaskId


@dataclass
class HarnessGraph:
    """One planner-led decomposition: planner + generator DAG.

    The graph's ``root_task_id`` points at the executor that launched the
    planner via ``request_plan``. The root executor is not inside any harness
    graph.

    Note fields:

    - ``root_goal`` / ``request_plan_note`` anchor every prompt rendered for
      this graph (captured once at graph creation).
    - ``handoff_plan_note`` is retained only for older persisted rows. New
      planner terminals use DAG task details plus ``what_to_do_next`` for
      partial continuation state.

    Structural slots: ``planner`` and ``dag_nodes`` mirror
    ``planner_task_id`` and the full planner-emitted DAG. ``plan_shape``,
    ``what_to_do_next``, and ``prior_graph_id`` carry partial-plan
    continuation state.
    """

    id: HarnessGraphId
    run_id: str
    root_task_id: TaskId
    planner_task_id: TaskId
    root_goal: str = ""
    request_plan_note: str = ""
    handoff_plan_note: str = ""
    executor_task_ids: list[TaskId] = field(default_factory=list)

    # Stage 1 structural slots (kept in sync with legacy fields).
    planner: TaskId = ""
    dag_nodes: list[TaskId] = field(default_factory=list)

    # Stage 3 — populated by Orchestrator.materialize_*_plan.
    plan_shape: Literal["full", "partial"] | None = None
    what_to_do_next: str = ""

    # Stage 5 — back-link for partial-plan continuation chains.
    prior_graph_id: HarnessGraphId | None = None

    def __post_init__(self) -> None:
        # Stage 1: keep new slots in sync with legacy field defaults.
        if not self.planner:
            self.planner = self.planner_task_id
        if not self.dag_nodes and self.executor_task_ids:
            self.dag_nodes = list(self.executor_task_ids)
