"""Built-in tool for querying background task status."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult

from ._common import TASK_ID_FIELD, apply_last_n_lines


class CheckBackgroundProgressInput(BaseModel):
    """Input for check_background_progress tool."""
    task_id: str = TASK_ID_FIELD
    last_n_lines: int = Field(
        default=20,
        ge=1,
        description="Number of output lines to include for completed tasks. Use to limit verbose output.",
    )


class CheckBackgroundProgressTool(BaseTool):
    """Query the status of background tasks (non-blocking)."""

    name: str = "check_background_progress"
    description: str = (
        "Check the current status of background tasks (non-blocking). Returns an instant snapshot "
        "of task status. Pass an exact task_id like \"bg_1\" or \"all\" to target every task. "
        "For blocking wait, use wait_for_background_task instead."
    )
    input_model: type[BaseModel] = CheckBackgroundProgressInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(
                output=(
                    "ERROR: background task manager is not available in this "
                    "context — no background tasks can be queried."
                ),
                is_error=True,
            )

        target_id = None if arguments.task_id == "all" else arguments.task_id
        status = manager.get_status(task_id=target_id)

        if not status:
            if target_id is not None:
                return ToolResult(
                    output=f"No background task found with ID: {target_id}",
                    is_error=True,
                )
            return ToolResult(output="No background tasks.", is_error=False)

        apply_last_n_lines(status, arguments.last_n_lines)
        return ToolResult(output=json.dumps(status, indent=2), is_error=False)

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True
