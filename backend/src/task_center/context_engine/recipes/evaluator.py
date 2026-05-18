"""``evaluator`` recipe — context for one evaluator spawn.

Emits goal/iteration framing, the current attempt plan, completed-task
summaries, and the evaluation criteria. The criteria block remains last so
pass/fail authority is anchored to the current attempt contract.

XML shape:

* Goal frame from :func:`goal_iteration_blocks`.
* ``<attempt_plan>`` group with ``<plan_spec>`` and, on a continues-goal
  attempt, ``<next_iteration_handoff_goal>`` child.
* ``<completed_tasks>`` group with one ``<task id="..." status="...">`` per
  generator task.
* ``<evaluation_criteria>`` standalone block.

The previous ``PARTIAL_PLAN_BOUNDARY`` block is gone: the structural signal
travels via the nested ``<next_iteration_handoff_goal>`` child, and the
behavioral guidance survives in :func:`evaluator_instruction(is_partial=True)`.
"""

from __future__ import annotations

from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.core import ContextEngineDeps
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes.goal_iteration_frame import (
    attempt_plan_blocks,
    goal_iteration_blocks,
    latest_summary_text,
)
from task_center.context_engine.recipes.role_instruction import (
    evaluator_instruction,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

EVALUATOR_ID = "evaluator"
_REQUIRED_FIELDS = frozenset({"goal_id", "attempt_id"})

_COMPLETED_TASKS_GROUP_PREFIX = "completed_tasks_"


def _evaluator_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    attempt = deps.attempt_store.get(scope.attempt_id)
    if attempt is None:
        raise ContextEngineError(f"Attempt {scope.attempt_id!r} not found")
    goal = deps.goal_store.get(scope.goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {scope.goal_id!r} not found")
    iteration_id = scope.iteration_id or attempt.iteration_id
    iteration = deps.iteration_store.get(iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {iteration_id!r} not found")

    blocks = goal_iteration_blocks(
        goal=goal,
        current_iteration=iteration,
        iterations=deps.iteration_store.list_for_goal(goal.id),
    )
    blocks.extend(attempt_plan_blocks(attempt, priority=ContextPriority.REQUIRED))

    blocks.extend(_completed_tasks_blocks(attempt, deps))

    blocks.append(
        evaluator_instruction(is_partial=bool(attempt.next_iteration_handoff_goal))
    )
    criteria = list(attempt.evaluation_criteria)
    if criteria:
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.EVALUATION_CRITERIA,
                priority=ContextPriority.REQUIRED,
                text="\n".join(f"- {c}" for c in criteria),
                source_id=attempt.id,
                source_kind="attempt",
                metadata={"tag": "evaluation_criteria"},
            )
        )

    return ContextPacket(
        target_role="evaluator",
        target_id=scope.attempt_id,
        canonical_refs=ContextRefs(
            goal_id=scope.goal_id,
            iteration_id=iteration.id,
            attempt_id=scope.attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


def _completed_tasks_blocks(attempt, deps: ContextEngineDeps) -> list[ContextBlock]:
    if not attempt.generator_task_ids:
        return []
    group_id = f"{_COMPLETED_TASKS_GROUP_PREFIX}{attempt.id}"
    blocks: list[ContextBlock] = []
    for task_id in attempt.generator_task_ids:
        task = deps.task_store.get_task(task_id)
        if task is None:
            # ``generator_task_ids`` are planner-submitted DAG nodes persisted
            # on the attempt; a missing row here is a harness invariant
            # violation, not a tolerable absence.
            raise ContextEngineError(
                f"Generator task {task_id!r} referenced by attempt is missing; "
                "evaluator context cannot be assembled without dependency results."
            )
        status = str(task.get("status") or "unknown")
        blocks.append(
            ContextBlock(
                kind=ContextBlockKind.COMPLETED_TASK_SUMMARY,
                priority=ContextPriority.HIGH,
                text=latest_summary_text(task.get("summaries")),
                source_id=task_id,
                source_kind="task_center_task",
                metadata={
                    "group_id": group_id,
                    "group_tag": "completed_tasks",
                    "child_tag": "task",
                    "attrs": f'id="{task_id}" status="{status}"',
                },
            )
        )
    return blocks


EVALUATOR_RECIPE = ContextRecipe(
    id=EVALUATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_evaluator_build,
)
