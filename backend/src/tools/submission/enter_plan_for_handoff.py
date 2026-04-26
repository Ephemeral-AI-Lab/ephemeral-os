"""Mode-entry tool: executor commits to plan_for_handoff mode."""

from __future__ import annotations

from pydantic import BaseModel

from agents.briefings import PLAN_FOR_HANDOFF_BRIEFING
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._mode_entry import enter_secondary_mode
from tools.submission._models import SubmissionOutput


class EnterPlanForHandoffInput(BaseModel):
    """Mode-entry tools take no arguments — entry is the commitment itself."""

    pass


@tool(
    name="enter_plan_for_handoff",
    description=(
        "Mode-entry (executor-only): commit to plan_for_handoff mode and read "
        "the briefing. From this mode the only exit is submit_plan_handoff. "
        "Idempotent if already in plan_for_handoff. Rejects from a subagent "
        "context or if the task is already in any other secondary mode."
    ),
    input_model=EnterPlanForHandoffInput,
    output_model=SubmissionOutput,
    is_mode_entry_tool=True,
)
async def enter_plan_for_handoff(*, context: ToolExecutionContext) -> ToolResult:
    return enter_secondary_mode(
        context,
        target_mode="plan_for_handoff",
        required_role="executor",
        briefing=PLAN_FOR_HANDOFF_BRIEFING,
        tool_name="enter_plan_for_handoff",
    )
