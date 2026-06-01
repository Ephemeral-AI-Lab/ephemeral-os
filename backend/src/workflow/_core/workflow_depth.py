"""Workflow ancestry depth helpers."""

from __future__ import annotations

from typing import Any

from workflow._core.primitives import (
    TaskCenterInvariantViolation,
    attempt_id_from_task_id,
)


def workflow_depth(*, workflow_id: str, deps: Any) -> int:
    """Return workflow ancestry depth, counting ``workflow_id`` itself."""
    depth = 0
    seen: set[str] = set()
    current = workflow_id
    while True:
        if current in seen:
            raise TaskCenterInvariantViolation("Cycle detected while resolving workflow ancestry.")
        seen.add(current)
        depth += 1

        workflow = deps.workflow_store.get(current)
        if workflow is None:
            raise TaskCenterInvariantViolation(f"Workflow {current!r} was not found.")
        if workflow.parent_task_id is None:
            return depth

        parent_attempt_id = attempt_id_from_task_id(workflow.parent_task_id)
        if parent_attempt_id is None:
            return depth

        parent_attempt = deps.attempt_store.get(parent_attempt_id)
        if parent_attempt is None:
            raise TaskCenterInvariantViolation(
                f"Parent Attempt {parent_attempt_id!r} was not found."
            )
        parent_iteration = deps.iteration_store.get(parent_attempt.iteration_id)
        if parent_iteration is None:
            raise TaskCenterInvariantViolation(
                f"Parent Iteration {parent_attempt.iteration_id!r} was not found."
            )
        current = parent_iteration.workflow_id


def is_nested_workflow(*, workflow_id: str | None, deps: Any) -> bool:
    if workflow_id is None:
        return False
    return workflow_depth(workflow_id=workflow_id, deps=deps) > 1


__all__ = ["is_nested_workflow", "workflow_depth"]
