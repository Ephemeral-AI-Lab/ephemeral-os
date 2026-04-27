"""Planner launch context construction."""

from __future__ import annotations

from dataclasses import dataclass

from task_center.model import HarnessGraph


@dataclass
class PlannerLaunchContext:
    """Structural input for a planner task, frozen as the planner's task input.

    Sourced entirely from the freshly-created ``HarnessGraph``: the planner
    sees only the root goal of its planning unit and the request note that
    spawned the planning unit.
    """

    root_goal: str
    request_plan_note: str

    def to_planner_input(self) -> str:
        return "\n\n".join(
            [
                f"## ROOT_GOAL\n{self.root_goal}",
                f"## REQUEST_PLAN_NOTE\n{self.request_plan_note}",
            ]
        )


def build_planner_launch_context(graph: HarnessGraph) -> PlannerLaunchContext:
    """Assemble planner input from the harness graph's stored notes."""
    return PlannerLaunchContext(
        root_goal=graph.root_goal,
        request_plan_note=graph.request_plan_note,
    )
