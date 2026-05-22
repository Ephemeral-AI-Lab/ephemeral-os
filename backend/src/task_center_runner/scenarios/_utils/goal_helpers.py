"""Goal predicate helpers used by recursive-goal-aware scenarios."""

from __future__ import annotations

from task_center_runner.scenarios.base import ScenarioContext


def is_entry_origin_goal(ctx: ScenarioContext) -> bool:
    """True when the scenario context is in the entry-origin goal."""
    goal = ctx.goal
    if goal is None:
        return True
    origin_kind = getattr(goal, "origin_kind", None)
    if str(getattr(origin_kind, "value", origin_kind) or "") == "entry":
        return True
    requested_by = str(goal.requested_by_task_id or "")
    return not requested_by


def is_recursive_goal(ctx: ScenarioContext) -> bool:
    """True when the scenario context is inside a child Goal."""
    return not is_entry_origin_goal(ctx)


__all__ = ["is_recursive_goal", "is_entry_origin_goal"]
