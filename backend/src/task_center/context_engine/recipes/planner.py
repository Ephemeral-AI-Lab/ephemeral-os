"""``planner`` recipe — context for one attempt planner spawn.

See plan §3.3.6 for the full block taxonomy. The recipe reads:

* the goal / current iteration frame;
* every prior closed-succeeded iteration projection for iteration 2+;
* every failed attempt in the current iteration except the running one
  (``failed_attempt_landscape`` blocks, ordered by ``attempt_sequence_no``).
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlockKind,
    ContextPacket,
    ContextRefs,
)
from task_center.context_engine.recipes.goal_iteration_frame import (
    goal_iteration_blocks,
)
from task_center.context_engine.recipes.attempt_landscape import (
    failed_attempt_landscape_blocks,
)
from task_center.context_engine.recipes.role_instruction import (
    planner_instruction,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

PLANNER_ID = "planner"
_REQUIRED_FIELDS = frozenset({"goal_id", "iteration_id", "attempt_id"})


def _planner_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    goal = deps.goal_store.get(scope.goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {scope.goal_id!r} not found")
    iteration = deps.iteration_store.get(scope.iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {scope.iteration_id!r} not found")

    blocks = goal_iteration_blocks(
        goal=goal,
        current_iteration=iteration,
        iterations=deps.iteration_store.list_for_goal(goal.id),
    )
    blocks.extend(
        failed_attempt_landscape_blocks(
            current_attempt_id=scope.attempt_id,
            attempts=deps.attempt_store.list_for_iteration(iteration.id),
            task_store=deps.task_store,
        )
    )
    has_failed_attempts = any(
        b.kind == ContextBlockKind.FAILED_ATTEMPT_LANDSCAPE for b in blocks
    )
    blocks.append(
        planner_instruction(
            iteration_sequence_no=iteration.sequence_no,
            has_failed_attempts=has_failed_attempts,
        )
    )

    return ContextPacket(
        target_role="planner",
        target_id=scope.attempt_id,
        canonical_refs=ContextRefs(
            goal_id=goal.id,
            iteration_id=iteration.id,
            attempt_id=scope.attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


PLANNER_RECIPE = ContextRecipe(
    id=PLANNER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_planner_build,
)
