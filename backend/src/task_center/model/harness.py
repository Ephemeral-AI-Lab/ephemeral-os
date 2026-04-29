"""HarnessGraph — the planner/executor/evaluator decomposition unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from task_center.model.task import HarnessGraphId, TaskId


@dataclass
class HarnessGraph:
    """One planner-led decomposition: planner + executor children + evaluator.

    The graph's ``root_task_id`` points at the executor or evaluator that
    launched the planner via ``request_plan``. The root executor is not
    inside any harness graph.

    The four note fields anchor every prompt rendered for this graph:

    - ``root_goal`` — the input of the immediate caller (the parent task)
      that invoked ``request_plan``. Captured once at graph creation.
    - ``request_plan_note`` — the verbatim ``request_plan_note`` argument
      the caller passed when invoking ``request_plan``. Captured once.
    - ``handoff_plan_note`` — the planner's ``handoff_plan_note`` from
      ``submit_plan_handoff`` (plan shape, topology, coverage map, GAP).
    - ``evaluator_note`` — the planner's explicit instruction to the
      evaluator from ``submit_plan_handoff`` (what to verify, what to
      skip, which adversarial probes are most relevant). Stored as the
      evaluator task's input.

    Stage 1 of the four-role/orchestrator roadmap adds three structural
    slots — ``planner``, ``dag_nodes``, ``evaluator`` — that mirror the
    legacy ``planner_task_id`` / ``executor_task_ids`` / ``evaluator_task_id``
    fields. The legacy fields are preserved so existing callers keep
    working; the new slots are kept in sync at construction time and on
    every mutation. Stage 5 adds ``prior_graph_id`` for partial-plan
    continuation chains.
    """

    id: HarnessGraphId
    run_id: str
    root_task_id: TaskId
    planner_task_id: TaskId
    root_goal: str = ""
    request_plan_note: str = ""
    handoff_plan_note: str = ""
    evaluator_note: str = ""
    evaluator_task_id: TaskId | None = None
    executor_task_ids: list[TaskId] = field(default_factory=list)

    # Stage 1 structural slots (kept in sync with legacy fields).
    planner: TaskId = ""
    dag_nodes: list[TaskId] = field(default_factory=list)
    evaluator: TaskId | None = None

    # Stage 3 — populated by Orchestrator.materialize_*_plan.
    plan_shape: Literal["full", "partial"] | None = None
    what_to_do_next: str = ""

    # Stage 5 — back-link for partial-plan continuation chains.
    prior_graph_id: HarnessGraphId | None = None

    def __post_init__(self) -> None:
        # Stage 1: keep new slots in sync with legacy field defaults.
        if not self.planner:
            self.planner = self.planner_task_id
        if self.evaluator is None and self.evaluator_task_id is not None:
            self.evaluator = self.evaluator_task_id
        if not self.dag_nodes and self.executor_task_ids:
            self.dag_nodes = list(self.executor_task_ids)
