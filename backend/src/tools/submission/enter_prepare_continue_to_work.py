"""Mode-entry tool: evaluator commits to prepare_continue_to_work mode."""

from __future__ import annotations

from pydantic import BaseModel

from agents.briefings import PREPARE_CONTINUE_TO_WORK_BRIEFING
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._mode_entry import enter_secondary_mode
from tools.submission._models import SubmissionOutput


class EnterPrepareContinueToWorkInput(BaseModel):
    """Mode-entry tools take no arguments — entry is the commitment itself."""

    pass


@tool(
    name="enter_prepare_continue_to_work",
    description=(
        "Mode-entry (evaluator-only): commit to prepare_continue_to_work mode "
        "and read the briefing. From this mode the only exit is "
        "submit_continue_to_work. Idempotent if already in "
        "prepare_continue_to_work. Rejects from a subagent context or if the "
        "task is already in any other secondary mode."
    ),
    input_model=EnterPrepareContinueToWorkInput,
    output_model=SubmissionOutput,
    is_mode_entry_tool=True,
)
async def enter_prepare_continue_to_work(
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    return enter_secondary_mode(
        context,
        target_mode="prepare_continue_to_work",
        required_role="evaluator",
        briefing=PREPARE_CONTINUE_TO_WORK_BRIEFING,
        tool_name="enter_prepare_continue_to_work",
    )
