"""Submission toolkit — terminal actions for team-mode agents."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


# ---------------------------------------------------------------------------
# DoneTool
# ---------------------------------------------------------------------------


class DoneInput(BaseModel):
    summary: str = Field(
        ...,
        description="1-3 sentence summary of what you accomplished.",
        min_length=1,
    )


class DoneTool(BaseTool):
    name = "done"
    description = "Signal task completion with a summary. Must be called exactly once."
    input_model = DoneInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, DoneInput)
        from team.models import Note, SubmittedSummary
        import time
        import uuid

        summary = arguments.summary.strip()
        if not summary:
            return ToolResult(output="Error: summary must be non-empty", is_error=True)
        submission = SubmittedSummary(summary=summary)
        context.metadata["submitted_output"] = submission
        tc = context.metadata.get("task_center")
        if tc:
            tc.post(
                Note(
                    id=str(uuid.uuid4()),
                    task_id=context.metadata.get("work_item_id", ""),
                    agent_name=context.metadata.get("agent_name", ""),
                    content=summary,
                    timestamp=time.time(),
                    scope_paths=list(context.metadata.get("write_scope") or []),
                )
            )
        return ToolResult(output=f"Summary accepted ({len(summary)} chars).")


# ---------------------------------------------------------------------------
# SubmitPlanTool
# ---------------------------------------------------------------------------


class SubmitPlanInput(BaseModel):
    tasks: list[dict] = Field(
        ...,
        description="List of TaskSpec dicts with id, task, agent, deps, scope_paths",
    )
    rationale: str | None = Field(
        default=None,
        description="Why this decomposition was chosen",
    )


class SubmitPlanTool(BaseTool):
    name = "submit_plan"
    description = "Submit a plan. Terminal action for planners."
    input_model = SubmitPlanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitPlanInput)
        from team.models import Plan

        plan = Plan.from_dict({"tasks": arguments.tasks, "rationale": arguments.rationale})
        if not plan.tasks:
            if not context.metadata.get("allow_empty_plan"):
                return ToolResult(output="Error: plan has no tasks", is_error=True)
        context.metadata["submitted_output"] = plan
        return ToolResult(output=f"Plan accepted ({len(plan.tasks)} tasks).")


# ---------------------------------------------------------------------------
# RequestRetryTool
# ---------------------------------------------------------------------------


class RequestRetryInput(BaseModel):
    reason: str = Field(..., description="Why retry is needed")


class RequestRetryTool(BaseTool):
    name = "request_retry"
    description = "Request a retry of the current task."
    input_model = RequestRetryInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, RequestRetryInput)
        from team.models import RetryRequest

        context.metadata["submitted_output"] = RetryRequest(reason=arguments.reason)
        return ToolResult(output="Retry requested.")


# ---------------------------------------------------------------------------
# RequestReplanTool
# ---------------------------------------------------------------------------


class RequestReplanInput(BaseModel):
    reason: str = Field(..., description="Why replan is needed")
    suggestion: str | None = Field(default=None, description="Suggestion for the replanner")


class RequestReplanTool(BaseTool):
    name = "request_replan"
    description = "Request a replan of the current task scope."
    input_model = RequestReplanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, RequestReplanInput)
        from team.models import ReplanRequest

        context.metadata["submitted_output"] = ReplanRequest(
            reason=arguments.reason,
            suggestion=arguments.suggestion,
        )
        return ToolResult(output="Replan requested.")


# ---------------------------------------------------------------------------
# SubmitReplanTool
# ---------------------------------------------------------------------------


class SubmitReplanInput(BaseModel):
    add_tasks: list[dict] = Field(default_factory=list, description="New tasks to add")
    cancel_ids: list[str] = Field(default_factory=list, description="Task IDs to cancel")


class SubmitReplanTool(BaseTool):
    name = "submit_replan"
    description = "Submit a corrective replan. Terminal action for replanners."
    input_model = SubmitReplanInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, SubmitReplanInput)
        from team.models import ReplanPlan

        replan = ReplanPlan.from_dict(
            {"add_tasks": arguments.add_tasks, "cancel_ids": arguments.cancel_ids}
        )
        context.metadata["submitted_output"] = replan
        count = len(replan.add_tasks)
        return ToolResult(
            output=f"Replan accepted ({count} new tasks, {len(replan.cancel_ids)} cancelled)."
        )


# ---------------------------------------------------------------------------
# SubmissionToolkit
# ---------------------------------------------------------------------------


class SubmissionToolkit(BaseToolkit):
    """Role-aware toolkit that exposes the appropriate terminal submission tools."""

    @classmethod
    def from_context(cls, ctx: object) -> SubmissionToolkit:
        from agents.registry import has_role

        agent_name: str = getattr(ctx, "metadata", {}).get("agent_name") or ""  # type: ignore[union-attr]
        if has_role(agent_name, "planner"):
            tools = [SubmitPlanTool()]
        elif has_role(agent_name, "replanner"):
            tools = [SubmitReplanTool()]
        else:
            tools = [DoneTool(), RequestRetryTool(), RequestReplanTool()]
        return cls(
            name="submission",
            description="Terminal submission actions for the current agent role.",
            tools=tools,
        )
