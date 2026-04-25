"""Accessor tool: read the direct children of a task (recursive opacity)."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._models import TaskGraphOutput


class ReadTaskGraphInput(BaseModel):
    task_id: str = Field(..., min_length=1, description="The parent task id whose direct children to list.")


@tool(
    name="read_task_graph",
    description=(
        "Return the DIRECT children of the given task with their id, title, "
        "status, and summary. Grandchildren are opaque — this is the recursive "
        "opacity invariant."
    ),
    input_model=ReadTaskGraphInput,
    output_model=TaskGraphOutput,
)
async def read_task_graph(
    task_id: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    tc = context.metadata.get("task_center")
    if tc is None:
        return ToolResult(output="read_task_graph: missing task_center", is_error=True)
    try:
        kids = tc.graph.children_of(task_id)
    except Exception as exc:
        return ToolResult(output=f"task {task_id!r} not found: {exc}", is_error=True)
    payload = {
        "children": [
            {
                "id": c.id,
                "title": c.title,
                "status": c.status.value,
                "summary": c.summary,
            }
            for c in kids
        ]
    }
    return ToolResult(output=json.dumps(payload))
