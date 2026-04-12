"""Edit history toolkit — query cross-run edit patterns."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


class QueryEditHistoryInput(BaseModel):
    paths: list[str] = Field(..., description="Scope paths to query edit history for")


class QueryEditHistoryTool(BaseTool):
    name = "query_edit_history"
    description = "Query cross-run edit patterns to predict scope conflicts. Planner-only."
    input_model = QueryEditHistoryInput

    async def execute(self, arguments: QueryEditHistoryInput, context: ToolExecutionContext) -> ToolResult:
        # In-memory stub: no cross-run history available without PostgreSQL
        return ToolResult(output=json.dumps({
            "hotspots": [],
            "note": "No cross-run history available (in-memory mode).",
        }))


class EditHistoryToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="edit_history",
            description="Query cross-run edit patterns to predict scope conflicts.",
            tools=[QueryEditHistoryTool()],
        )

    @classmethod
    def from_context(cls, ctx: Any) -> EditHistoryToolkit:
        return cls()
