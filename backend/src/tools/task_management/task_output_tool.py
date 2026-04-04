"""Tool for reading task output."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tasks.manager import get_task_manager
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskOutputToolInput(BaseModel):
    """Arguments for task output retrieval."""

    task_id: str = Field(description="Task identifier")
    max_bytes: int = Field(default=12000, ge=1, le=100000)


class TaskOutputTool(BaseTool):
    """Read the output of a background task."""

    name = "task_output"
    description = "Read the output log for a background task."
    input_model = TaskOutputToolInput

    def is_read_only(self, arguments: TaskOutputToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskOutputToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            output = get_task_manager().read_task_output(arguments.task_id, max_bytes=arguments.max_bytes)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=output or "(no output)")
