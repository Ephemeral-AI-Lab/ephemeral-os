"""Built-in tool for querying background task status."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class CheckBackgroundProgressInput(BaseModel):
    """Input for check_background_progress tool."""
    task_id: str | None = Field(
        default=None,
        description="Optional task ID to filter. If omitted, returns all background tasks.",
    )
    last_n_lines: int = Field(
        default=20,
        description="Number of output lines to include for completed tasks. Use to limit verbose output.",
    )


class CheckBackgroundProgressTool(BaseTool):
    """Query the status of background tasks.

    Returns a list of all background tasks with their current status
    (running, completed, failed, cancelled), elapsed time, and output
    if completed.
    """

    name: str = "check_background_progress"
    description: str = (
        "Check the current status of background tasks (non-blocking). Returns an instant snapshot "
        "of task status. Use this BEFORE wait_for_background_task to review what is running. "
        "For blocking wait, use wait_for_background_task instead."
    )
    input_model: type[BaseModel] = CheckBackgroundProgressInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(output="No background tasks are running.", is_error=False)

        assert isinstance(arguments, CheckBackgroundProgressInput)
        status = manager.get_status(task_id=arguments.task_id)

        if not status:
            if arguments.task_id:
                return ToolResult(
                    output=f"No background task found with ID: {arguments.task_id}",
                    is_error=True,
                )
            return ToolResult(output="No background tasks.", is_error=False)

        for entry in status:
            if "output" in entry and entry["output"]:
                lines = entry["output"].splitlines()
                entry["output"] = "\n".join(lines[-arguments.last_n_lines:])

        return ToolResult(output=json.dumps(status, indent=2), is_error=False)

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True
