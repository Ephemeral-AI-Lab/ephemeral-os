"""Foreground workspace pipeline selection for daemon tool handlers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sandbox._shared.models import Intent, ToolCallRequest, ToolCallResult
from sandbox._shared.workspace_pipeline import WorkspacePipeline
from sandbox.daemon.request_context import require_layer_stack_root, required_single_path
from sandbox.ephemeral_workspace.pipeline import get_sandbox_overlay
from sandbox.isolated_workspace import get_active_pipeline


async def resolve_pipeline(req: ToolCallRequest) -> WorkspacePipeline:
    """Return isolated pipeline for open iws handles, otherwise ephemeral."""
    iws = get_active_pipeline()
    if iws is not None and iws.get_handle(req.agent_id) is not None:
        return iws
    return await get_sandbox_overlay(
        require_layer_stack_root(req.args),
        start=False,
    )


async def run_tool_handler(
    args: dict[str, Any],
    *,
    verb: str,
    intent: Intent,
) -> ToolCallResult:
    if verb in {"read_file", "write_file", "edit_file"}:
        required_single_path(args)
    agent_id = _agent_id(args)
    req = ToolCallRequest(
        request_id=str(args.get("request_id") or uuid4().hex),
        agent_id=agent_id,
        verb=verb,
        intent=intent,
        args=args,
        actor_id=str(args.get("actor_id") or ""),
        background=bool(args.get("background", False)),
    )
    pipeline = await resolve_pipeline(req)
    return await pipeline.run_tool_call(req)


def _agent_id(args: dict[str, Any]) -> str:
    raw = str(args.get("agent_id") or args.get("actor_id") or "default").strip()
    return raw or "default"


__all__ = ["resolve_pipeline", "run_tool_handler"]
