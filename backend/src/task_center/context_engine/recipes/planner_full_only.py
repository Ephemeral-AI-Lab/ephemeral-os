"""``planner_full_only`` recipe — context for a leaf planner spawn.

The ``planner_full_only`` variant is resolved when a planner is delegated a
child goal at nested depth > 1 (see ``task_center/_core/agent_routing.py``).
Its launch-time context is a single ``<goal>`` block — no
``<iteration status="prior">`` projections, no ``<iteration status="current">``
group, no failed-attempt landscape. The agent has exactly one terminal
(``submit_plan_closes_goal``); there is no defer option, no retry frame, and
no prior-iteration evidence to consult because the child goal is the leaf.

This matches the spec at
``docs/reports/initial_messages_cases/OPTIMIZED_USER_MSG_1.md`` Case 12.
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
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope


PLANNER_FULL_ONLY_ID = "planner_full_only"
_REQUIRED_FIELDS = frozenset({"goal_id"})


def _planner_full_only_build(
    scope: ContextScope, deps: ContextEngineDeps
) -> ContextPacket:
    goal = deps.goal_store.get(scope.goal_id)
    if goal is None:
        raise ContextEngineError(f"Goal {scope.goal_id!r} not found")
    block = ContextBlock(
        kind=ContextBlockKind.GOAL_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=goal.goal,
        source_id=goal.id,
        source_kind="goal",
        metadata={"tag": "goal"},
    )
    return ContextPacket(
        target_role="planner",
        target_id=scope.attempt_id,
        canonical_refs=ContextRefs(
            goal_id=goal.id,
            iteration_id=scope.iteration_id,
            attempt_id=scope.attempt_id,
        ),
        blocks=[block],
        source_ids=[goal.id],
    )


PLANNER_FULL_ONLY_RECIPE = ContextRecipe(
    id=PLANNER_FULL_ONLY_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_planner_full_only_build,
)
