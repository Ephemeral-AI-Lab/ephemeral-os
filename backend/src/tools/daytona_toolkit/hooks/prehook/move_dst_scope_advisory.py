"""Advisory for move destinations outside write scope."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import (
    _team_repo_write_warning,
    _write_scope_covers,
    resolved_arg,
)


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    dst = resolved_arg(args, "target_path", context)
    if dst is None:
        return PreHookOutcome()
    src = resolved_arg(args, "src_path", context)
    if src is not None and _write_scope_covers(context, src):
        return PreHookOutcome()
    warning = _team_repo_write_warning(context, dst, tool_name=tool_name)
    if warning is None:
        return PreHookOutcome()
    return PreHookOutcome(advisories=(warning,))


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_move_file",
        "pre",
        20,
        hook,
        name="daytona_move_file:dst_scope_advisory",
    )
