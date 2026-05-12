"""Planner submission rejection scenarios.

Each scenario emits an invalid ``submit_full_plan`` / ``submit_partial_plan``
and asserts the attempt closes with ``fail_reason="planner_failed"``, no
generator/evaluator ran, and the right ``TaskCenterInvariantViolation`` was
surfaced.

Implemented (reference scenarios):
- :class:`PlannerCycleInDeps`
- :class:`PlannerDuplicateLocalId`
- :class:`PlannerEmptyTasks`
- :class:`PlannerPartialWithoutContinuationGoal`
- :class:`PlannerUnknownAgentName`
- :class:`PlannerUnknownDep`
"""

from __future__ import annotations

from live_e2e.scenarios.planner_validation.cycle_in_deps import (
    PlannerCycleInDeps,
)
from live_e2e.scenarios.planner_validation.duplicate_local_id import (
    PlannerDuplicateLocalId,
)
from live_e2e.scenarios.planner_validation.empty_tasks import PlannerEmptyTasks
from live_e2e.scenarios.planner_validation.partial_without_continuation_goal import (
    PlannerPartialWithoutContinuationGoal,
)
from live_e2e.scenarios.planner_validation.unknown_agent_name import (
    PlannerUnknownAgentName,
)
from live_e2e.scenarios.planner_validation.unknown_dep import PlannerUnknownDep

__all__ = [
    "PlannerCycleInDeps",
    "PlannerDuplicateLocalId",
    "PlannerEmptyTasks",
    "PlannerPartialWithoutContinuationGoal",
    "PlannerUnknownAgentName",
    "PlannerUnknownDep",
]
