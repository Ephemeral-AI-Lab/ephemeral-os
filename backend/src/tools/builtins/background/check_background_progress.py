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


class CheckBackgroundProgressTool(BaseTool):
    """Query the status of background tasks.

    Returns a list of all background tasks with their current status
    (running, completed, failed, cancelled), elapsed time, and output
    if completed.
    """

    name: str = "check_background_progress"
    description: str = (
        "Check the status of background tasks. Returns task ID, tool name, "
        "status, elapsed time, and recent output for each background task. "
        "Use this to monitor long-running operations."
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

        return ToolResult(output=json.dumps(status, indent=2), is_error=False)

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True
