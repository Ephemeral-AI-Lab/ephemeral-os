"""``generator`` recipe — context for one generator task spawn.

Emits the generator's ``<needs>`` (upstream outcomes) followed by its assigned
task. XML shape:

* ``<needs>`` group with one ``<task id="..." status="...">`` child per
  upstream task, omitted when the assigned task has no needs.
* ``<assigned_task task_id="...">`` — the generator's local contract, anchored
  last so the agent ends on its concrete obligation.

Symmetric with the reducer recipe (``<needs>`` + an assigned block); there is no
global ``<plan_spec>`` — the planner distributes framing into each task_spec.

The ``<Task Guidance>`` row is assembled at launch time by
``AgentEntryComposer`` via the registry-driven
``task_center/context_engine/task_guidance.py:build_task_guidance``.
"""

from __future__ import annotations

from task_center.context_engine.engine import ContextEngineDeps
from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes._needs import needs_outcome_blocks
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope


GENERATOR_ID = "generator"
_REQUIRED_FIELDS = frozenset({"workflow_id", "attempt_id", "task_id"})


def build_generator_context(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    attempt_id = scope.require_field("attempt_id")
    task_id = scope.require_field("task_id")
    workflow_id = scope.require_field("workflow_id")

    attempt = deps.attempt_store.get(attempt_id)
    if attempt is None:
        raise ContextEngineError(f"Attempt {attempt_id!r} not found")
    iteration_id = scope.iteration_id or attempt.iteration_id
    task = deps.task_store.get_task(task_id)
    if task is None:
        raise ContextEngineError(f"TaskCenterTask {task_id!r} not found")

    needs = tuple(str(dep) for dep in task.get("needs") or ())
    blocks: list[ContextBlock] = list(
        needs_outcome_blocks(needs=needs, task_store=deps.task_store)
    )
    blocks.append(
        ContextBlock(
            kind=ContextBlockKind.PLANNED_TASK_SPEC,
            priority=ContextPriority.REQUIRED,
            text=str(task.get("context_message") or ""),
            source_id=task_id,
            source_kind="task_center_task",
            metadata={
                "tag": "assigned_task",
                "attrs": f'task_id="{task_id}"',
            },
        )
    )

    return ContextPacket(
        target_role="generator",
        target_id=task_id,
        canonical_refs=ContextRefs(
            workflow_id=workflow_id,
            iteration_id=iteration_id or attempt.iteration_id,
            attempt_id=attempt_id,
            task_id=task_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )

GENERATOR_RECIPE = ContextRecipe(
    id=GENERATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=build_generator_context,
)
