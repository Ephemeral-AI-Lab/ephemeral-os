"""Pipeline context tools — query outputs from completed pipeline steps."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult


def _serialize(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# query_pipeline_context
# ---------------------------------------------------------------------------


class _QueryInput(BaseModel):
    step: str = Field(description="Step name to query.")
    key: str | None = Field(
        default=None,
        description="Optional specific output key.  If omitted, returns the entire step output.",
    )


class QueryPipelineContextTool(BaseTool):
    """Query structured output from a completed pipeline step."""

    name = "query_pipeline_context"
    description = (
        "Query structured output from a completed pipeline step. "
        "Use this to read data produced by earlier steps."
    )
    input_model = _QueryInput

    def __init__(self, *, context_map: dict[str, dict] | None = None) -> None:
        self._context_map = context_map or {}

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(
        self, arguments: _QueryInput, context: ToolExecutionContext
    ) -> ToolResult:
        step_output = self._context_map.get(arguments.step)
        if step_output is None:
            available = list(self._context_map.keys())
            return ToolResult(
                output=json.dumps({
                    "error": f"Step '{arguments.step}' has no recorded output.",
                    "available_steps": available,
                }),
                is_error=True,
            )

        if arguments.key is not None:
            value = step_output.get(arguments.key)
            if value is None:
                return ToolResult(
                    output=json.dumps({
                        "error": f"Key '{arguments.key}' not found in step '{arguments.step}' output.",
                        "available_keys": list(step_output.keys()),
                    }),
                    is_error=True,
                )
            return ToolResult(output=_serialize({arguments.key: value}))

        return ToolResult(output=_serialize(step_output))


# ---------------------------------------------------------------------------
# list_pipeline_steps
# ---------------------------------------------------------------------------


class _EmptyInput(BaseModel):
    pass


class ListPipelineStepsTool(BaseTool):
    """List all completed pipeline steps and their output keys."""

    name = "list_pipeline_steps"
    description = (
        "List all completed pipeline steps and their output keys. "
        "Use this to discover what data is available from prior steps."
    )
    input_model = _EmptyInput

    def __init__(self, *, context_map: dict[str, dict] | None = None) -> None:
        self._context_map = context_map or {}

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(
        self, arguments: _EmptyInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not self._context_map:
            return ToolResult(
                output=json.dumps({"steps": [], "note": "No step outputs recorded yet."})
            )
        steps = [
            {"step": name, "keys": list(output.keys())}
            for name, output in self._context_map.items()
        ]
        return ToolResult(output=json.dumps({"steps": steps}))


# ---------------------------------------------------------------------------
# get_pipeline_metadata
# ---------------------------------------------------------------------------


class GetPipelineMetadataTool(BaseTool):
    """Get pipeline-level metadata (goal, config, current step)."""

    name = "get_pipeline_metadata"
    description = (
        "Get pipeline-level metadata including the goal, current step, "
        "and pipeline configuration."
    )
    input_model = _EmptyInput

    def __init__(
        self,
        *,
        pipeline_meta: dict | None = None,
        current_step: str | None = None,
    ) -> None:
        self._pipeline_meta = pipeline_meta or {}
        self._current_step = current_step

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(
        self, arguments: _EmptyInput, context: ToolExecutionContext
    ) -> ToolResult:
        meta = dict(self._pipeline_meta)
        meta["current_step"] = self._current_step
        return ToolResult(output=_serialize(meta))
