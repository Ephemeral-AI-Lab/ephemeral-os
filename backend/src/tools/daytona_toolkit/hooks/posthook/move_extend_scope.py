"""Post-hook: widen write_scope to the move destination when src was owned.

A move whose src lives inside the caller's ``write_scope`` is a rename-like
operation — ownership should follow the path. Before the platform-hook
migration the tool body captured ``src_in_scope`` before commit and applied
the extension on success. Now that OCC commit results surface
``changed_paths`` in the tool result metadata, the policy fits naturally as
a post-hook on ``daytona_move_file``: observe success, re-check ``src`` is in
scope, extend ``write_scope`` to the resolved destination, and emit an advisory
so the agent sees the updated scope in a system notification.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.hooks import PostHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit._daytona_utils import (
    _extend_write_scope,
    _resolve_path,
    _write_scope_covers,
)


def _scope_added_advisory(context: ToolExecutionContext, added_path: str) -> str:
    current = context.metadata.get("write_scope")
    if isinstance(current, list):
        scope_paths = [str(path) for path in current]
    else:
        scope_paths = []
    rendered_scope = ", ".join(scope_paths) if scope_paths else "<none>"
    return (
        f"Scope path added: {added_path}. "
        f"Current scope_paths: {rendered_scope}."
    )


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

    src_path = getattr(args, "src_path", None)
    target_path = getattr(args, "target_path", None)
    if not isinstance(src_path, str) or not isinstance(target_path, str):
        return PostHookOutcome()

    if not _write_scope_covers(context, _resolve_path(src_path, context)):
        return PostHookOutcome()

    added_path = _extend_write_scope(context, _resolve_path(target_path, context))
    if added_path is None:
        return PostHookOutcome()
    return PostHookOutcome(advisories=(_scope_added_advisory(context, added_path),))


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_move_file",
        "post",
        10,
        hook,
        name="daytona_move_file:extend_write_scope_on_success",
    )
