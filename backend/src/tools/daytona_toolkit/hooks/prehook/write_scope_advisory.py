"""Advisory for single-path writes outside write scope."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import _team_repo_write_warning, resolved_arg

_PATH_FIELDS = {
    "daytona_edit_file": "file_path",
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
    warning = _team_repo_write_warning(context, path, tool_name=tool_name)
    if warning is None:
        return PreHookOutcome()
    return PreHookOutcome(advisories=(warning,))


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    for tool_name in _PATH_FIELDS:
        reg.register(
            tool_name,
            "pre",
            20,
            hook,
            name=f"{tool_name}:write_scope_advisory",
        )
