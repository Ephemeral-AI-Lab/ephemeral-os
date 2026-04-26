"""PlannerLaunchContext — the structured input passed to a new planner task.

Built by ``TaskCenter.launch_plan_handoff``. Executor callers get first-time
decomposition context (caller goal + upstream handoff summaries). Evaluator
callers get recovery context with prior plan, successful work, failed work,
and dependency-blocked work.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from task_center.task import TaskId, TaskSummary


@dataclass
class PlannerLaunchContext:
    task_detail: str
    caller_task_id: TaskId
    caller_role: Literal["executor", "evaluator"]
    requested_goal: str
    upstream_handoff_summaries: list[TaskSummary] = field(default_factory=list)
    prior_planner_handoff: list[TaskSummary] = field(default_factory=list)
    completed_child_summaries: list[TaskSummary] = field(default_factory=list)
    failed_child_summaries: list[TaskSummary] = field(default_factory=list)
    dependency_blocked_summaries: list[TaskSummary] = field(default_factory=list)

    def to_planner_input(self) -> str:
        """Render the context as a JSON string used as the planner's task input."""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)
