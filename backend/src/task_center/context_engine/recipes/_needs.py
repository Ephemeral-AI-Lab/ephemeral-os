"""Shared ``<needs>`` block builder for the generator and reducer recipes.

Both roles open on their upstream results: one ``<needs>`` group wrapping one
``<task id="..." status="...">`` child per task in ``needs``. The body of each
child is the upstream task's outcome text (or its handoff roll-up).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center._core.outcomes import task_outcome_from_row
from task_center.context_engine.exceptions import ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes._task_xml import block_task_body, task_attrs

if TYPE_CHECKING:
    from task_center._core.persistence import TaskStoreProtocol


_NEEDS_GROUP_ID = "needs"


def needs_outcome_blocks(
    *,
    needs: tuple[str, ...],
    task_store: TaskStoreProtocol,
) -> list[ContextBlock]:
    """Emit a ``<needs>`` group with one ``<task>`` child per upstream task."""
    if not needs:
        return []
    out: list[ContextBlock] = []
    for dep_id in needs:
        dep = task_store.get_task(dep_id)
        if dep is None:
            # ``needs`` are persisted DAG edges validated at planner-submission
            # acceptance; a missing row here is a harness invariant violation.
            raise ContextEngineError(
                f"Need task {dep_id!r} is missing; context cannot be assembled "
                "without its result."
            )
        outcome = task_outcome_from_row(dep_id, dep)
        text, pre_rendered = block_task_body(outcome)
        metadata = {
            "group_id": _NEEDS_GROUP_ID,
            "group_tag": "needs",
            "child_tag": "task",
            "attrs": task_attrs(outcome),
        }
        if pre_rendered:
            metadata["pre_rendered_xml"] = "true"
        out.append(
            ContextBlock(
                kind=ContextBlockKind.DEPENDENCY_SUMMARY,
                priority=ContextPriority.MEDIUM,
                text=text,
                source_id=dep_id,
                source_kind="task_center_task",
                metadata=metadata,
            )
        )
    return out


__all__ = ["needs_outcome_blocks"]
