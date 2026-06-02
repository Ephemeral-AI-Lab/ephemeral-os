"""Workflow-origin predicates used by delegated-workflow scenarios."""

from __future__ import annotations

from test_runner.scenarios.base import ScenarioContext


def is_entry_origin_workflow(ctx: ScenarioContext) -> bool:
    """True for the root request agent or its root-launched workflow."""
    if ctx.workflow is None:
        return True
    return str(getattr(ctx.workflow, "parent_task_id", "") or "").startswith("root-")


def is_recursive_workflow(ctx: ScenarioContext) -> bool:
    """True when the scenario context is inside a delegated Workflow."""
    return not is_entry_origin_workflow(ctx)


__all__ = ["is_recursive_workflow", "is_entry_origin_workflow"]
