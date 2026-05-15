"""Goal predicate helpers used by recursive-goal-aware scenarios."""

from __future__ import annotations

from task_center_runner.scenarios.base import ScenarioContext


def is_root_goal(ctx: ScenarioContext) -> bool:
    """True when the scenario context is in the entry-spawned root goal."""
    goal = ctx.goal
    if goal is None:
        return True
    requested_by = str(goal.requested_by_task_id or "")
    return requested_by.endswith(":entry")


def is_recursive_goal(ctx: ScenarioContext) -> bool:
    """True when the scenario context is inside a child Goal."""
    return not is_root_goal(ctx)


__all__ = ["is_recursive_goal", "is_root_goal"]
