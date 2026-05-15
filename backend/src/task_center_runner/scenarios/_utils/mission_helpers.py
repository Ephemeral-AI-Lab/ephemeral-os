"""Mission predicate helpers used by recursive-mission-aware scenarios."""

from __future__ import annotations

from task_center_runner.scenarios.base import ScenarioContext


def is_root_mission(ctx: ScenarioContext) -> bool:
    """True when the scenario context is in the entry-spawned root mission."""
    mission = ctx.mission
    if mission is None:
        return True
    requested_by = str(mission.requested_by_task_id or "")
    return requested_by.endswith(":entry")


def is_recursive_mission(ctx: ScenarioContext) -> bool:
    """True when the scenario context is inside a child Mission."""
    return not is_root_mission(ctx)


__all__ = ["is_recursive_mission", "is_root_mission"]
