"""Hard-block unauthorized move-source test-file writes."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import _team_repo_write_error, resolved_arg


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    path = resolved_arg(args, "src_path", context)
    if path is None:
        return PreHookOutcome()
    err = _team_repo_write_error(context, path, tool_name=tool_name)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_move_file",
        "pre",
        10,
        hook,
        name="daytona_move_file:src_hard_block",
    )
