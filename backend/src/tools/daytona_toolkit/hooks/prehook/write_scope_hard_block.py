"""Hard-block unauthorized test-file writes."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import _team_repo_write_error, resolved_arg

_PATH_FIELDS = {
    "daytona_write_file": "file_path",
    "daytona_edit_file": "file_path",
    "daytona_delete_file": "path",
}


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    field = _PATH_FIELDS.get(tool_name)
    path = resolved_arg(args, field, context) if field is not None else None
    if path is None:
        return PreHookOutcome()
    err = _team_repo_write_error(context, path, tool_name=tool_name)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    for tool_name in _PATH_FIELDS:
        reg.register(
            tool_name,
            "pre",
            10,
            hook,
            name=f"{tool_name}:write_scope_hard_block",
        )
