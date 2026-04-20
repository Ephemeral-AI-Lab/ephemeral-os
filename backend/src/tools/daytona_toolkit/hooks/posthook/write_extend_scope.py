"""Post-hook: widen write_scope to successful write targets.

Outside-scope ``daytona_write_file`` calls are allowed when justified. Once a
call succeeds and the OCC commit reports changed paths, the widened edit should
become part of the lane's in-memory write scope so follow-up writes to the same
new file do not trip downstream scope checks.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.hooks import PostHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit._daytona_utils import _extend_write_scope, _resolve_path


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
    result: ToolResult,
) -> PostHookOutcome:
    del tool_name
    if result.is_error:
        return PostHookOutcome()
    changed = result.metadata.get("changed_paths")
    if not isinstance(changed, list) or not changed:
        return PostHookOutcome()

    file_path = getattr(args, "file_path", None)
    if not isinstance(file_path, str):
        return PostHookOutcome()

    _extend_write_scope(context, _resolve_path(file_path, context))
    return PostHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_write_file",
        "post",
        10,
        hook,
        name="daytona_write_file:extend_write_scope_on_success",
    )
