"""Shared foreground workspace pipeline protocol."""

from __future__ import annotations

from typing import Protocol

from sandbox._shared.models import ToolCallRequest, ToolCallResult


class WorkspacePipeline(Protocol):
    """Workspace execution substrate with one foreground tool-call method."""

    async def run_tool_call(self, req: ToolCallRequest) -> ToolCallResult: ...


__all__ = ["WorkspacePipeline"]
