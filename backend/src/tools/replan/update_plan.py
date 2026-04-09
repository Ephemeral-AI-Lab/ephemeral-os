"""``update_plan`` tool — lateral DAG mutation for replanner agents.

This is a **work-phase tool** (not a posthook). It directly mutates the
dispatcher graph under lock, inserting corrective items at the same depth
and parent as the failed work item. This avoids the nesting problem where
``validate_plan_phase_b`` always creates children at ``depth + 1``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from team.models import WorkItemKind
from tools.core.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


class ReplanItemSpec(BaseModel):
    """Specification for a corrective work item added by the replanner."""

    agent_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    local_id: str | None = None
    deps: list[str] = Field(default_factory=list)
    notes: str | None = None
    timeout_seconds: float | None = None
    kind: WorkItemKind = WorkItemKind.ATOMIC
    briefings: list[dict[str, Any]] = Field(default_factory=list)


class UpdatePlanInput(BaseModel):
    """Input schema for the update_plan tool."""

    add_items: list[ReplanItemSpec] = Field(
        default_factory=list,
        description="New work items to add as siblings at the replan depth level.",
    )
    cancel_ids: list[str] = Field(
        default_factory=list,
        description="IDs of PENDING/READY work items to cancel (must share same parent).",
    )


class UpdatePlanTool(BaseTool):
    name: str = "update_plan"
    description: str = (
        "Mutate the running DAG: add new corrective work items at the failed "
        "node's depth level, and/or cancel stale pending items. New items are "
        "inserted as true siblings (same depth, same parent) of the failed "
        "work item. Call this exactly once with your corrective plan."
    )
    input_model = UpdatePlanInput

    async def execute(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> ToolResult:
        assert isinstance(arguments, UpdatePlanInput)

        team_run_id = context.metadata.get("team_run_id")
        replan_wi_id = context.metadata.get("work_item_id")
        if not team_run_id or not replan_wi_id:
            return ToolResult(
                output="Missing team_run_id or work_item_id in context.",
                is_error=True,
            )

        # Lazy import to avoid circular dependency
        from team.runtime.registry import get as get_team_run

        team_run = get_team_run(team_run_id)
        if team_run is None:
            return ToolResult(output=f"team_run {team_run_id} not found", is_error=True)

        dispatcher = team_run.dispatcher
        replan_wi = dispatcher.graph.get(replan_wi_id)
        if replan_wi is None:
            return ToolResult(
                output=f"replanner work item {replan_wi_id} not found",
                is_error=True,
            )

        failed_wi_id = (replan_wi.payload or {}).get("failed_work_item_id")
        if not failed_wi_id:
            return ToolResult(
                output="replanner payload missing failed_work_item_id",
                is_error=True,
            )

        failed_wi = dispatcher.graph.get(failed_wi_id)
        if failed_wi is None:
            return ToolResult(
                output=f"failed work item {failed_wi_id} not found",
                is_error=True,
            )

        try:
            result = await dispatcher.apply_replan(
                replan_wi_id=replan_wi_id,
                add_specs=[s.model_dump() for s in arguments.add_items],
                cancel_ids=arguments.cancel_ids,
                target_depth=failed_wi.depth,
                target_parent_id=failed_wi.parent_id,
                target_root_id=failed_wi.root_id,
            )
            return ToolResult(
                output=(
                    f"Replan applied: {result['added']} item(s) added, "
                    f"{result['cancelled']} item(s) cancelled."
                )
            )
        except Exception as exc:
            logger.warning("update_plan failed: %s", exc, exc_info=True)
            return ToolResult(output=f"Replan failed: {exc}", is_error=True)
