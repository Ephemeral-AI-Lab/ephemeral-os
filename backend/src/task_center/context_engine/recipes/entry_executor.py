"""``entry_executor`` recipe — context for the first task spawn after intake."""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

ENTRY_EXECUTOR_ID = "entry_executor"
_REQUIRED_FIELDS = frozenset({"task_id"})


def _entry_executor_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    task = deps.task_store.get_task(scope.task_id)
    if task is None:
        raise ContextEngineError(f"Entry task {scope.task_id!r} not found")
    block = ContextBlock(
        kind=ContextBlockKind.ENTRY_REQUEST,
        priority=ContextPriority.REQUIRED,
        text=str(task.get("context_message") or ""),
        source_id=scope.task_id,
        source_kind="task_center_task",
        metadata={"tag": "entry_request"},
    )
    return ContextPacket(
        target_role="executor",
        target_id=scope.task_id,
        canonical_refs=ContextRefs(task_id=scope.task_id),
        blocks=[block],
        source_ids=[scope.task_id],
    )


ENTRY_EXECUTOR_RECIPE = ContextRecipe(
    id=ENTRY_EXECUTOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_entry_executor_build,
)
