"""Accessor tool: read details of a task by id."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._models import TaskDetailsOutput


class ReadTaskDetailsInput(BaseModel):
    task_id: str = Field(..., min_length=1, description="The id of the task to read.")


@tool(
    name="read_task_details",
    description=(
        "Read the spec, acceptance_criteria, handoff_note, status, and summary "
        "for a task by id. Use to inspect your own task or any sibling/ancestor "
        "you have access to."
    ),
    input_model=ReadTaskDetailsInput,
    output_model=TaskDetailsOutput,
)
async def read_task_details(
    task_id: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    tc = context.metadata.get("task_center")
    if tc is None:
        return ToolResult(output="read_task_details: missing task_center", is_error=True)
    try:
        task = tc.graph.get(task_id)
    except Exception as exc:
        return ToolResult(output=f"task {task_id!r} not found: {exc}", is_error=True)
    payload = {
        "title": task.title,
        "spec": task.spec,
        "status": task.status.value,
        "parent_id": task.parent_id,
        "acceptance_criteria": task.acceptance_criteria,
        "handoff_note": task.handoff_note,
        "summary": task.summary,
    }
    return ToolResult(output=json.dumps(payload))
