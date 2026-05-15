"""``evaluator`` recipe — context for one evaluator spawn.

Emits goal/iteration framing, the current trial plan, dependency results,
and the evaluation criteria in presentation order. The criteria block remains
last so pass/fail authority is anchored to the current trial contract.
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes._shared import (
    latest_summary_text,
    goal_iteration_blocks,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

EVALUATOR_ID = "evaluator"
_REQUIRED_FIELDS = frozenset({"goal_id", "trial_id"})


def _evaluator_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    trial = deps.trial_store.get(scope.trial_id)
    if trial is None:
        raise ContextEngineError(f"Trial {scope.trial_id!r} not found")
    goal = deps.goal_store.get(scope.goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {scope.goal_id!r} not found")
    iteration_id = scope.iteration_id or trial.iteration_id
    iteration = deps.iteration_store.get(iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {iteration_id!r} not found")

    blocks = goal_iteration_blocks(
        goal=goal,
        current_iteration=iteration,
        iterations=deps.iteration_store.list_for_goal(goal.id),
    )
    if trial.task_specification:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.TASK_SPECIFICATION,
                priority=ContextPriority.REQUIRED,
                text=trial.task_specification,
                source_id=trial.id,
                source_kind="trial",
            )
        )
    if trial.continuation_goal:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.PARTIAL_PLAN_BOUNDARY,
                priority=ContextPriority.REQUIRED,
                text=(
                    "plan_kind: partial\n"
                    f"continuation_goal: {trial.continuation_goal}\n\n"
                    "This trial is intentionally partial. If it passes, "
                    "the continuation_goal becomes the next iteration. Do not "
                    "treat continuation work as missing from the current "
                    "trial; judge this trial against the Trial Plan "
                    "and Evaluation Criteria."
                ),
                source_id=trial.id,
                source_kind="trial",
                metadata={"plan_kind": "partial"},
            )
        )

    for task_id in trial.generator_task_ids:
        task = deps.task_store.get_task(task_id)
        if task is None:
            # ``generator_task_ids`` are planner-submitted DAG nodes persisted
            # on the trial; a missing row here is a harness invariant
            # violation, not a tolerable absence.
            raise ContextEngineError(
                f"Generator task {task_id!r} referenced by trial is missing; "
                "evaluator context cannot be assembled without dependency results."
            )
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.COMPLETED_TASK_SUMMARY,
                priority=ContextPriority.HIGH,
                text=latest_summary_text(task.get("summaries")),
                source_id=task_id,
                source_kind="task_center_task",
                metadata={
                    "task_id": task_id,
                    "group_heading": "# Dependency Results",
                    "subheading": str(task.get("id") or task_id),
                },
            )
        )
    criteria = list(trial.evaluation_criteria)
    if criteria:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.EVALUATION_CRITERIA,
                priority=ContextPriority.REQUIRED,
                text="\n".join(f"- {c}" for c in criteria),
                source_id=trial.id,
                source_kind="trial",
            )
        )

    return ContextPacket(
        target_role="evaluator",
        target_id=scope.trial_id,
        canonical_refs=ContextRefs(
            goal_id=scope.goal_id,
            iteration_id=iteration.id,
            trial_id=scope.trial_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


EVALUATOR_RECIPE = ContextRecipe(
    id=EVALUATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_evaluator_build,
)
