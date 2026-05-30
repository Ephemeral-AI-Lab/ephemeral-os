"""Workflow-origin predicates used by recursive-handoff scenarios."""

from __future__ import annotations

from task_center_runner.scenarios.base import ScenarioContext


def is_entry_origin_workflow(ctx: ScenarioContext) -> bool:
    """True when the scenario context is in the entry (root) workflow.

    Every workflow links back to its spawning task via ``parent_task_id``. The
    root workflow's parent is the synthetic run-level bootstrap task
    ``<run_id>:root``; child (recursively spawned) workflows point at a
    generator task id, so the ``:root`` suffix distinguishes them.
    """
    workflow = ctx.workflow
    if workflow is None:
        return True
    return (workflow.parent_task_id or "").endswith(":root")


def is_recursive_workflow(ctx: ScenarioContext) -> bool:
    """True when the scenario context is inside a child Workflow."""
    return not is_entry_origin_workflow(ctx)


__all__ = ["is_recursive_workflow", "is_entry_origin_workflow"]
