"""``evaluator`` recipe — context for one evaluator spawn.

Emits goal/iteration framing followed by the failed-prior attempts and the
current attempt — every ``<attempt>`` is a child of the active
``<iteration status="current">`` group. The attempt body inlines
``<plan_spec>``, optional ``<deferred_goal_for_next_iteration>``, per-task
``<task>`` summaries, and ``<evaluation_criteria>`` as siblings (no
``<attempt_plan>`` / ``<completed_tasks>`` wrappers).
"""

from __future__ import annotations

from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.core import ContextEngineDeps
from task_center.context_engine.packet import (
    ContextPacket,
    ContextRefs,
)
from task_center.context_engine.recipes.attempts import (
    current_attempt_block,
    failed_attempt_blocks,
)
from task_center.context_engine.recipes.iterations import (
    goal_iteration_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

EVALUATOR_ID = "evaluator"
_REQUIRED_FIELDS = frozenset({"goal_id", "attempt_id"})


def _evaluator_build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    attempt_id = scope.require_field("attempt_id")
    goal_id = scope.require_field("goal_id")

    attempt = deps.attempt_store.get(attempt_id)
    if attempt is None:
        raise ContextEngineError(f"Attempt {attempt_id!r} not found")
    goal = deps.goal_store.get(goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {goal_id!r} not found")
    iteration_id = scope.iteration_id or attempt.iteration_id
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
            current_attempt_id=attempt.id,
            iteration=iteration,
            attempts=deps.attempt_store.list_for_iteration(iteration.id),
            task_store=deps.task_store,
        )
    )
    blocks.extend(
        current_attempt_block(
            attempt=attempt,
            iteration=iteration,
            task_store=deps.task_store,
        )
    )

    return ContextPacket(
        target_role="evaluator",
        target_id=attempt_id,
        canonical_refs=ContextRefs(
            goal_id=goal_id,
            iteration_id=iteration.id,
            attempt_id=attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


EVALUATOR_RECIPE = ContextRecipe(
    id=EVALUATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_evaluator_build,
)
