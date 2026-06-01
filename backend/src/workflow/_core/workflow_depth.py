"""Workflow ancestry depth helpers."""

from __future__ import annotations

from typing import Any

from workflow._core.primitives import WorkflowInvariantViolation


def workflow_depth(*, workflow_id: str, deps: Any) -> int:
    """Return workflow ancestry depth, counting ``workflow_id`` itself."""
    depth = 0
    seen: set[str] = set()
    current = workflow_id
    while True:
        if current in seen:
            raise WorkflowInvariantViolation("Cycle detected while resolving workflow ancestry.")
        seen.add(current)
        depth += 1

        workflow = deps.workflow_store.get(current)
        if workflow is None:
            raise WorkflowInvariantViolation(f"Workflow {current!r} was not found.")
        parent_task = deps.task_store.get_task(workflow.parent_task_id)
        if parent_task is None:
            raise WorkflowInvariantViolation(
                f"Parent task {workflow.parent_task_id!r} was not found."
            )
        parent_attempt_id = str(parent_task.get("attempt_id") or "")
        if not parent_attempt_id:
            return depth

        parent_attempt = deps.attempt_store.get(parent_attempt_id)
        if parent_attempt is None:
            raise WorkflowInvariantViolation(
                f"Parent Attempt {parent_attempt_id!r} was not found."
            )
        parent_iteration = deps.iteration_store.get(parent_attempt.iteration_id)
        if parent_iteration is None:
            raise WorkflowInvariantViolation(
                f"Parent Iteration {parent_attempt.iteration_id!r} was not found."
            )
        current = parent_iteration.workflow_id


def is_nested_workflow(*, workflow_id: str | None, deps: Any) -> bool:
    if workflow_id is None:
        return False
    return workflow_depth(workflow_id=workflow_id, deps=deps) > 1


__all__ = ["is_nested_workflow", "workflow_depth"]
