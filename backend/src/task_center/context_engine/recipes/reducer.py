"""``reducer`` recipe — context for one reducer task spawn.

A reducer digests its ``<needs>`` (upstream outcomes) and gates against its
``<assigned_prompt>``. Symmetric with the generator recipe: ``<needs>`` + an
assigned block. A reducer sees ONLY its needs + its own prompt — no
attempt-wide plan or all-generator view (a convergent reducer that needs every
generator recovers the global view by construction).
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


REDUCER_ID = "reducer"
_REQUIRED_FIELDS = frozenset({"workflow_id", "attempt_id", "task_id"})


def build_reducer_context(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
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
                "tag": "assigned_prompt",
                "attrs": f'task_id="{task_id}"',
            },
        )
    )

    return ContextPacket(
        target_role="reducer",
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


REDUCER_RECIPE = ContextRecipe(
    id=REDUCER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=build_reducer_context,
)
