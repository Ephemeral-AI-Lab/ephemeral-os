"""Agent-facing isolated workspace exit tool."""

from __future__ import annotations

import json

from pydantic import BaseModel

from sandbox._shared.models import ExitIsolatedWorkspaceRequest, Intent
from sandbox.host.iws_lifecycle import exit_isolated_workspace as lifecycle_exit
from tools._framework.core.base import (
    TextToolOutput,
    ToolExecutionContextService,
    ToolResult,
)
from tools._framework.core.decorator import tool
from tools.sandbox._lib.session import caller_from_context


class ExitIsolatedWorkspaceInput(BaseModel):
    grace_s: float = 5.0


@tool(
    name="exit_isolated_workspace",
    description="Close and discard this agent's isolated workspace.",
    short_description="Exit isolated workspace.",
    input_model=ExitIsolatedWorkspaceInput,
    output_model=TextToolOutput,
    intent=Intent.LIFECYCLE,
)
async def exit_isolated_workspace(
    grace_s: float = 5.0,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    result = await lifecycle_exit(
        ExitIsolatedWorkspaceRequest(
            caller=caller_from_context(context),
            grace_s=grace_s,
            description="exit isolated workspace",
        ),
        background_manager=context.get("background_task_manager"),
    )
    return ToolResult(output=json.dumps(_payload(result), indent=2), is_error=not result.success)


def _payload(result: object) -> dict[str, object]:
    error = getattr(result, "error", None)
    return {
        "success": bool(getattr(result, "success", False)),
        "evicted_upperdir_bytes": getattr(result, "evicted_upperdir_bytes", 0),
        "lifetime_s": getattr(result, "lifetime_s", 0.0),
        "phases_ms": getattr(result, "phases_ms", {}),
        "error": None if error is None else {
            "kind": error.kind,
            "message": error.message,
            "details": error.details,
        },
    }


__all__ = ["ExitIsolatedWorkspaceInput", "exit_isolated_workspace"]
