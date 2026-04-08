"""``submit_plan`` tool — stashes a validated Plan in ``ctx.tool_metadata``.

The tool imports ``team.validation.validate_plan_phase_a`` (a pure function)
and ``team.types.Plan``. The ``team/ → tools/`` dependency direction is
preserved at runtime: the Worker — not the tool — is responsible for
handing the extracted Plan to the Dispatcher. The tool never touches
Dispatcher state.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from team.types import Plan
from team.validation import validate_plan_phase_a
from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class _SubmitPlanItem(BaseModel):
    agent_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    local_id: str | None = None
    deps: list[str] = Field(default_factory=list)
    notes: str | None = None
    timeout_seconds: float | None = None


class SubmitPlanInput(BaseModel):
    items: list[_SubmitPlanItem]
    rationale: str | None = None


class SubmitPlanTool(BaseTool):
    name: str = "submit_plan"
    description: str = (
        "Submit a Plan to extend the team's DAG. Each item names an existing "
        "agent and an optional list of dependency local_ids or external "
        "work_item_ids. Validation runs synchronously: if any structural "
        "issue is found the tool returns a structured error and you MUST "
        "fix it and call submit_plan again."
    )
    input_model = SubmitPlanInput

    async def execute(
        self, arguments: SubmitPlanInput, context: ToolExecutionContext
    ) -> ToolResult:
        metadata = context.metadata

        # Single-submission guard
        if metadata.get("submitted_plan") is not None:
            return ToolResult(
                output="submit_plan already called; second call ignored.",
                is_error=True,
            )

        # Parse into domain Plan
        try:
            plan = Plan.from_dict(arguments.model_dump())
        except Exception as exc:
            return ToolResult(output=f"Invalid Plan shape: {exc}", is_error=True)

        max_plan_size = int(metadata.get("max_plan_size", 50) or 50)
        issues = validate_plan_phase_a(plan, max_plan_size=max_plan_size)
        if issues:
            lines = [f"- {i['field']}: {i['msg']}" for i in issues]
            return ToolResult(
                output=(
                    "invalid_plan:\n"
                    + "\n".join(lines)
                    + "\n\nFix the issues above and call submit_plan again."
                ),
                is_error=True,
            )

        # Stash for the Worker to extract post-invocation.
        metadata["submitted_plan"] = plan
        return ToolResult(
            output=f"Plan accepted: {len(plan.items)} item(s) queued for dispatch."
        )
