"""Goal ancestry — nested-goal depth resolution.

Walks the parent-task / parent-attempt / parent-iteration chain to count how
many goals deep a given goal sits. Used by the agent-routing predicates that
still need nested-goal awareness.
"""

from __future__ import annotations

from task_center._core.persistence import (
    AttemptStoreProtocol,
    GoalStoreProtocol,
    IterationStoreProtocol,
    TaskStoreProtocol,
)
from task_center._core.primitives import TaskCenterInvariantViolation


def nested_goal_depth(
    *,
    goal_id: str,
    goal_store: GoalStoreProtocol,
    iteration_store: IterationStoreProtocol,
    attempt_store: AttemptStoreProtocol,
    task_store: TaskStoreProtocol,
) -> int:
    """Number of goal ancestors on the chain INCLUDING ``goal_id``."""
    depth = 0
    seen_goal_ids: set[str] = set()
    current_goal_id = goal_id
    while True:
        if current_goal_id in seen_goal_ids:
            raise TaskCenterInvariantViolation(
                "Cycle detected while resolving goal ancestry."
            )
        seen_goal_ids.add(current_goal_id)
        depth += 1
        current_goal = goal_store.get(current_goal_id)
        if current_goal is None:
            raise TaskCenterInvariantViolation(
                f"Goal {current_goal_id!r} was not found."
            )
        if current_goal.requested_by_task_id is None:
            return depth
        parent_task = task_store.get_task(current_goal.requested_by_task_id)
        if parent_task is None:
            return depth
        parent_attempt_id = str(parent_task.get("task_center_attempt_id") or "")
        if not parent_attempt_id:
            return depth
        parent_attempt = attempt_store.get(parent_attempt_id)
        if parent_attempt is None:
            raise TaskCenterInvariantViolation(
                f"Parent Attempt {parent_attempt_id!r} was not found."
            )
        parent_iteration = iteration_store.get(parent_attempt.iteration_id)
        if parent_iteration is None:
            raise TaskCenterInvariantViolation(
                f"Parent Iteration {parent_attempt.iteration_id!r} was not found."
            )
        current_goal_id = parent_iteration.goal_id


__all__ = ["nested_goal_depth"]
