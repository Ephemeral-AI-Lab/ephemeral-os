"""Tool for stopping tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tasks.manager import get_task_manager
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskStopToolInput(BaseModel):
    """Arguments for stopping a task."""

    task_id: str = Field(description="Task identifier")


class TaskStopTool(BaseTool):
    """Stop a background task."""

    name = "task_stop"
    description = "Stop a background task."
    input_model = TaskStopToolInput

    async def execute(self, arguments: TaskStopToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            task = await get_task_manager().stop_task(arguments.task_id)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"Stopped task {task.id}")
