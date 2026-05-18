"""Planner submission rejection scenarios.

Each scenario emits an invalid ``submit_plan_closes_goal`` / ``submit_plan_defers_goal``
and asserts the attempt closes with ``fail_reason="planner_failed"``, no
generator/evaluator ran, and the right ``TaskCenterInvariantViolation`` was
surfaced.

Implemented (reference scenarios):
- :class:`PlannerCycleInDeps`
- :class:`PlannerDuplicateLocalId`
- :class:`PlannerEmptyTasks`
- :class:`PlannerDefersWithoutDeferredGoal`
- :class:`PlannerUnknownAgentName`
- :class:`PlannerUnknownDep`
"""

from __future__ import annotations

from task_center_runner.scenarios.planner_validation.cycle_in_deps import (
    PlannerCycleInDeps,
)
from task_center_runner.scenarios.planner_validation.duplicate_local_id import (
    PlannerDuplicateLocalId,
)
from task_center_runner.scenarios.planner_validation.empty_tasks import PlannerEmptyTasks
from task_center_runner.scenarios.planner_validation.defers_without_deferred_goal import (
    PlannerDefersWithoutDeferredGoal,
)
from task_center_runner.scenarios.planner_validation.unknown_agent_name import (
    PlannerUnknownAgentName,
)
from task_center_runner.scenarios.planner_validation.unknown_dep import PlannerUnknownDep

__all__ = [
    "PlannerCycleInDeps",
    "PlannerDuplicateLocalId",
    "PlannerEmptyTasks",
    "PlannerDefersWithoutDeferredGoal",
    "PlannerUnknownAgentName",
    "PlannerUnknownDep",
]
