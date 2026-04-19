"""Advisory post-hook for ambient changed paths produced by CodeAct shells.

CodeAct's ``svc.cmd`` overlay audit distinguishes files the command was
expected to write (``changed_paths``) from files it touched as a side
effect (``ambient_changed_paths``). Coordinated CodeAct shells are
runtime-only — ambient changes to the workspace are concurrent drift, not
authorised edits. Surface them as a user-only advisory so operators can
see the drift without interrupting the agent.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.hooks import PostHookOutcome, ToolHookRegistry, default_registry

_MAX_RENDERED = 5


def _format(paths: list[str]) -> str:
    rendered = ", ".join(paths[:_MAX_RENDERED])
    if len(paths) > _MAX_RENDERED:
        rendered += f", ... ({len(paths)} total)"
    return (
        "Workspace changed during this shell command, but coordinated CodeAct "
        "shell commands are runtime-only; treating changed paths as ambient "
        f"concurrent edits: {rendered}"
    )


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
    result: ToolResult,
) -> PostHookOutcome:
    del tool_name, args, context
    raw = result.metadata.get("ambient_changed_paths")
    if not isinstance(raw, list):
        return PostHookOutcome()
    paths = [str(p) for p in raw if str(p or "").strip()]
    if not paths:
        return PostHookOutcome()
    return PostHookOutcome(advisories=(_format(paths),))


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "post",
        20,
        hook,
        name="daytona_codeact:ambient_change_warning",
    )
