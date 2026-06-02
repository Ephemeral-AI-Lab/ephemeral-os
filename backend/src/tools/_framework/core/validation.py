"""Tool input/output validation helpers."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, RootModel, ValidationError

from tools._framework.core.results import ToolInputParseResult, ToolResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tools._framework.core.base import BaseTool
    from tools._framework.core.context import ToolExecutionContextService


def parse_tool_input(
    tool: BaseTool,
    raw_input: dict[str, Any],
) -> ToolInputParseResult:
    """Validate raw tool input against the tool's pydantic model."""
    if "background" in raw_input and "background" not in tool.input_model.model_fields:
        return ToolInputParseResult.failure(
            ToolResult(
                output=(
                    f"Invalid input for {tool.name}: `background` is not a tool "
                    "argument. Use typed subagent or command-session controls instead."
                ),
                is_error=True,
            )
        )
    try:
        parsed_input = tool.input_model.model_validate(raw_input)
    except ValidationError as exc:
        errors = _format_validation_errors(exc)
        return ToolInputParseResult.failure(
            ToolResult(
                output=(
                    f"Invalid input for {tool.name}: {errors}. "
                    "Please retry the tool call with valid arguments."
                ),
                is_error=True,
            )
        )
    except Exception as exc:
        # Not a pydantic ValidationError: either `raw_input` is not a
        # mapping, or a custom validator raised something exotic. This is
        # an internal error path, not an "invalid arguments" path — do not
        # tell the agent to retry; surface the type so triage can find it.
        logger.exception("Internal validation error for tool %s", tool.name)
        return ToolInputParseResult.failure(
            ToolResult(
                output=(
                    f"Internal validation error for {tool.name}: "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
            )
        )
    return ToolInputParseResult.success(parsed_input)


async def execute_tool_body(
    tool: BaseTool,
    parsed_input: BaseModel,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Execute a tool with already validated input and normalize exceptions."""
    try:
        return await tool.execute(parsed_input, context)
    except Exception as exc:
        # Render the exception type alongside its message so production
        # triage can tell `ValueError` from `KeyError` without parsing
        # prose. The full traceback is logged (not surfaced to the LLM) to
        # keep tool output bounded.
        logger.exception("Tool execution failed: %s", tool.name)
        return ToolResult(
            output=f"Tool execution failed: {type(exc).__name__}: {exc}",
            is_error=True,
        )


def validate_tool_output(tool: BaseTool, result: ToolResult) -> ToolResult:
    """Validate successful tool output against the tool's declared output model."""
    if result.is_error:
        return result

    model = tool.output_model
    try:
        if issubclass(model, RootModel):
            model.model_validate(result.output)
        else:
            try:
                payload = json.loads(result.output)
            except json.JSONDecodeError as exc:
                return ToolResult(
                    output=(
                        f"Invalid output from {tool.name}: expected JSON matching "
                        f"{model.__name__}, got non-JSON output ({exc.msg})."
                    ),
                    is_error=True,
                    metadata={
                        **result.metadata,
                        "output_validation_error": exc.msg,
                    },
                )
            model.model_validate(payload)
    except ValidationError as exc:
        errors = _format_validation_errors(exc)
        return ToolResult(
            output=(
                f"Invalid output from {tool.name}: output did not match "
                f"{model.__name__}: {errors}."
            ),
            is_error=True,
            metadata={
                **result.metadata,
                "output_validation_error": errors,
            },
        )
    return result


def _format_validation_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
