"""``planner`` recipe — context for one attempt planner spawn.

The recipe reads:

* the goal / current iteration frame;
* every prior closed-succeeded iteration projection for iteration 2+;
* every failed attempt in the current iteration except the running one
  (``failed_attempt`` blocks, ordered by ``attempt_sequence_no``).

The ``<Task Guidance>`` row is assembled at launch time by
``AgentEntryComposer`` via the registry-driven
``task_center/task_guidance/builders.py:build_task_guidance`` — recipes
emit only context blocks.
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextPacket,
    ContextRefs,
)
from task_center.context_engine.recipes.iterations import (
    goal_iteration_blocks,
)
from task_center.context_engine.recipes.attempts import (
    failed_attempt_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

PLANNER_ID = "planner"
_REQUIRED_FIELDS = frozenset({"goal_id", "iteration_id", "attempt_id"})


def _planner_build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    goal_id = scope.require_field("goal_id")
    iteration_id = scope.require_field("iteration_id")
    attempt_id = scope.require_field("attempt_id")

    goal = deps.goal_store.get(goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {goal_id!r} not found")
    iteration = deps.iteration_store.get(iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {iteration_id!r} not found")

    blocks = goal_iteration_blocks(
        goal=goal,
        current_iteration=iteration,
        iterations=deps.iteration_store.list_for_goal(goal.id),
    )
    blocks.extend(
        failed_attempt_blocks(
            current_attempt_id=attempt_id,
            iteration=iteration,
            attempts=deps.attempt_store.list_for_iteration(iteration.id),
            task_store=deps.task_store,
        )
    )

    return ContextPacket(
        target_role="planner",
        target_id=attempt_id,
        canonical_refs=ContextRefs(
            goal_id=goal.id,
            iteration_id=iteration.id,
            attempt_id=attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


PLANNER_RECIPE = ContextRecipe(
    id=PLANNER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_planner_build,
)
