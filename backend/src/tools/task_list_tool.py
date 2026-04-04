"""Tool for listing tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tasks.manager import get_task_manager
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskListToolInput(BaseModel):
    """Arguments for task listing."""

    status: str | None = Field(default=None, description="Optional status filter")


class TaskListTool(BaseTool):
    """List background tasks."""

    name = "task_list"
    description = "List background tasks."
    input_model = TaskListToolInput

    def is_read_only(self, arguments: TaskListToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskListToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        tasks = get_task_manager().list_tasks(status=arguments.status)  # type: ignore[arg-type]
        if not tasks:
            return ToolResult(output="(no tasks)")
        return ToolResult(
            output="\n".join(f"{task.id} {task.type} {task.status} {task.description}" for task in tasks)
        )
