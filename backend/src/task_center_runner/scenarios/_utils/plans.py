"""Plan-shape factories shared across focused scenarios.

Keep these minimal and orthogonal: each helper returns a plan that exercises
exactly one task_center configuration. Scenarios compose them per branch in
their `planner_response`.
"""

from __future__ import annotations

from typing import Any


def minimal_full_plan(
    *,
    task_specification: str,
    evaluation_criteria: list[str],
    task_id: str = "preflight",
    task_spec: str | None = None,
    agent_name: str = "executor",
) -> dict[str, Any]:
    """One-task full plan; the cheapest plan that drives the whole pipeline."""
    return {
        "task_specification": task_specification,
        "evaluation_criteria": evaluation_criteria,
        "tasks": [{"id": task_id, "agent_name": agent_name, "deps": []}],
        "task_specs": {task_id: task_spec or task_specification},
    }


def preflight_full_plan(
    *,
    task_specification: str = "Run a workspace preflight probe.",
    evaluation_criteria: tuple[str, ...] = (
        "Workspace preflight completed.",
    ),
) -> dict[str, Any]:
    """Full plan whose single task triggers the `preflight` executor action."""
    return minimal_full_plan(
        task_specification=task_specification,
        evaluation_criteria=list(evaluation_criteria),
        task_id="preflight",
        task_spec=(
            "Run a lightweight workspace preflight and report the observed "
            "sandbox root."
        ),
    )


def preflight_partial_plan(
    *,
    continuation_goal: str,
    task_specification: str = (
        "Run a workspace preflight probe and continue with the follow-up goal."
    ),
    evaluation_criteria: tuple[str, ...] = (
        "Workspace preflight completed.",
    ),
) -> dict[str, Any]:
    """Partial plan with continuation_goal; drives PARTIAL_CONTINUATION episode."""
    plan = minimal_full_plan(
        task_specification=task_specification,
        evaluation_criteria=list(evaluation_criteria),
        task_id="preflight",
        task_spec=(
            "Run a lightweight workspace preflight and report the observed "
            "sandbox root."
        ),
    )
    plan["continuation_goal"] = continuation_goal
    return plan


__all__ = [
    "minimal_full_plan",
    "preflight_full_plan",
    "preflight_partial_plan",
]
