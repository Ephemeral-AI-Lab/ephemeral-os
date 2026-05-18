"""``generator`` recipe — context for one generator task spawn.

Emits the current attempt plan, dependency results, and the assigned local
task. XML shape:

* ``<attempt_plan>`` group with ``<plan_spec>`` (and ``<next_iteration_handoff_goal>``
  when the parent attempt is a continues-goal plan).
* ``<dependency_results>`` group with one ``<dependency id="...">`` child per
  upstream task; omitted when the assigned task has no deps.
* ``<assigned_task task_id="...">`` standalone block — the generator's local
  contract, anchored last so the agent ends on its concrete obligation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    latest_summary_text,
)
from task_center.context_engine.recipes.role_instruction import (
    generator_instruction,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

if TYPE_CHECKING:
    from task_center._core.persistence import TaskStoreProtocol


GENERATOR_ID = "generator"
_REQUIRED_FIELDS = frozenset({"goal_id", "attempt_id", "task_id"})

_DEPENDENCY_RESULTS_GROUP_PREFIX = "dependency_results_"


def _generator_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    attempt = deps.attempt_store.get(scope.attempt_id)
    if attempt is None:
        raise ContextEngineError(f"Attempt {scope.attempt_id!r} not found")
    task = deps.task_store.get_task(scope.task_id)
    if task is None:
        raise ContextEngineError(f"TaskCenterTask {scope.task_id!r} not found")

    blocks: list[ContextBlock] = []
    blocks.extend(attempt_plan_blocks(attempt, priority=ContextPriority.HIGH))

    needs = tuple(str(dep) for dep in task.get("needs") or ())
    blocks.extend(
        _dependency_results_blocks(
            attempt_id=attempt.id,
            needs=needs,
            task_store=deps.task_store,
        )
    )
    blocks.append(generator_instruction(has_deps=bool(needs)))
    blocks.append(
        ContextBlock(
            kind=ContextBlockKind.PLANNED_TASK_SPEC,
            priority=ContextPriority.REQUIRED,
            text=str(task.get("context_message") or ""),
            source_id=scope.task_id,
            source_kind="task_center_task",
            metadata={
                "tag": "assigned_task",
                "attrs": f'task_id="{scope.task_id}"',
            },
        )
    )

    return ContextPacket(
        target_role="generator",
        target_id=scope.task_id,
        canonical_refs=ContextRefs(
            goal_id=scope.goal_id,
            iteration_id=scope.iteration_id or attempt.iteration_id,
            attempt_id=scope.attempt_id,
            task_id=scope.task_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


def _dependency_results_blocks(
    *,
    attempt_id: str,
    needs: tuple[str, ...],
    task_store: TaskStoreProtocol,
) -> list[ContextBlock]:
    if not needs:
        return []
    group_id = f"{_DEPENDENCY_RESULTS_GROUP_PREFIX}{attempt_id}"
    out: list[ContextBlock] = []
    for dep_id in needs:
        dep = task_store.get_task(dep_id)
        if dep is None:
            # ``needs`` are persisted DAG edges validated at planner-submission
            # acceptance; a missing row here is a harness invariant violation.
            raise ContextEngineError(
                f"Dependency task {dep_id!r} referenced by needs is missing; "
                "generator context cannot be assembled without dependency results."
            )
        out.append(
            ContextBlock(
                kind=ContextBlockKind.DEPENDENCY_SUMMARY,
                priority=ContextPriority.MEDIUM,
                text=latest_summary_text(dep.get("summaries")),
                source_id=dep_id,
                source_kind="task_center_task",
                metadata={
                    "group_id": group_id,
                    "group_tag": "dependency_results",
                    "child_tag": "dependency",
                    "attrs": f'id="{dep_id}"',
                },
            )
        )
    return out


GENERATOR_RECIPE = ContextRecipe(
    id=GENERATOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_generator_build,
)
