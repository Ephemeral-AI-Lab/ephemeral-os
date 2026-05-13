"""Tool input/output validation and schema decoration helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, RootModel, ValidationError

from tools._framework.core.results import ToolInputParseResult, ToolResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tools._framework.core.base import BaseTool
    from tools._framework.core.context import ToolExecutionContextService
    from tools._framework.core.registry import ToolRegistry


# Engine-level schema decorations that may be injected into a tool's
# input_schema by `decorate_schemas_for_background` (and any future
# runtime-control decorator). Keep this set in sync with the property
# names that `decorate_schemas_for_background` writes into the schema —
# anything added there MUST also be added here, or pydantic
# `extra="forbid"` models will reject the inflated input at validation
# time.
_RUNTIME_CONTROL_FIELDS: frozenset[str] = frozenset({"background"})


def parse_tool_input(
    tool: BaseTool,
    raw_input: dict[str, Any],
) -> ToolInputParseResult:
    """Validate raw tool input against the tool's pydantic model."""
    clean_input = _strip_runtime_control_fields(tool, raw_input)
    try:
        parsed_input = tool.input_model.model_validate(clean_input)
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
        # Not a pydantic ValidationError: either `clean_input` is not a
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


def decorate_schemas_for_background(
    registry: ToolRegistry,
    schemas: list[dict[str, Any]],
    *,
    terminal_tools: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Inject optional ``background`` fields for eligible non-terminal tools.

    Mutates each schema in-place and returns the list. Terminal tools are
    one-way submissions and must expose only their true payload schema.
    ``background`` is added only to non-terminal tools whose ``background``
    policy is ``"optional"`` (LLM may choose). Tools marked ``"always"`` are
    dispatched in the background unconditionally and need no LLM-facing flag.
    """
    terminal_tool_names = set(terminal_tools)
    for schema in schemas:
        tool_name = str(schema["name"])
        tool = registry.get(tool_name)
        inp = schema.setdefault("input_schema", {})
        props = inp.setdefault("properties", {})
        is_terminal = tool_name in terminal_tool_names
        if (
            not is_terminal
            and tool is not None
            and getattr(tool, "background", "forbidden") == "optional"
        ):
            # Keep in sync with `_RUNTIME_CONTROL_FIELDS` above. Any new
            # property key written into `props` here must also appear in
            # that set so `_strip_runtime_control_fields` removes it
            # before per-tool pydantic validation.
            props["background"] = {
                "type": "boolean",
                "description": (
                    "Set to true to run this tool asynchronously in the background. "
                    "This supports long-running operations such as builds, test suites, "
                    "and installs."
                ),
            }
    return schemas


def _format_validation_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )


def _strip_runtime_control_fields(tool: BaseTool, raw_input: dict[str, Any]) -> dict[str, Any]:
    """Remove engine-level schema decorations before tool-local validation."""

    model_fields = set(tool.input_model.model_fields)
    return {
        key: value
        for key, value in raw_input.items()
        if key not in _RUNTIME_CONTROL_FIELDS or key in model_fields
    }
