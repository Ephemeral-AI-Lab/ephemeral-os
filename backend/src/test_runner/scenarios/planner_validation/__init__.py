"""Planner submission rejection scenarios.

Each scenario emits an invalid ``submit_planner_outcome`` payload and asserts
the attempt closes with ``fail_reason="task_failed"`` and no generator or
reducer task was created (the submission is rejected at the planner tool
boundary or the ``ordered_plan_tasks`` gate).

Implemented (reference scenarios):
- :class:`PlannerCycleInDeps`
- :class:`PlannerDuplicateLocalId`
- :class:`PlannerEmptyTasks`
- :class:`PlannerBlankDeferredGoal`
- :class:`PlannerUnknownAgentName`
- :class:`PlannerUnknownDep`
"""

from __future__ import annotations

from test_runner.scenarios.planner_validation.cycle_in_deps import (
    PlannerCycleInDeps,
)
from test_runner.scenarios.planner_validation.duplicate_local_id import (
    PlannerDuplicateLocalId,
)
from test_runner.scenarios.planner_validation.empty_tasks import PlannerEmptyTasks
from test_runner.scenarios.planner_validation.blank_deferred_goal import (
    PlannerBlankDeferredGoal,
)
from test_runner.scenarios.planner_validation.unknown_agent_name import (
    PlannerUnknownAgentName,
)
from test_runner.scenarios.planner_validation.unknown_dep import PlannerUnknownDep

__all__ = [
    "PlannerCycleInDeps",
    "PlannerDuplicateLocalId",
    "PlannerEmptyTasks",
    "PlannerBlankDeferredGoal",
    "PlannerUnknownAgentName",
    "PlannerUnknownDep",
]
