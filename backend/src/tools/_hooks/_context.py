"""Shared context readers for tool hooks.

Hooks resolve the calling agent's identity the same way the sandbox caller
does (``agent_run_id`` then ``agent_name``); centralizing it keeps the daemon
in-flight and isolated-status queries keyed consistently with
``sandbox_caller_from_tool_context``. This module reads only the passed-in
context and imports no sandbox modules, so it stays clear of the
isolated-workspace import cycle the hook modules deliberately avoid.
"""

from __future__ import annotations

from tools._framework.core.context import ToolExecutionContextService


def resolve_agent_id(context: ToolExecutionContextService) -> str:
    """Return the calling agent's id: ``agent_run_id`` then ``agent_name``."""
    return str(context.get("agent_run_id") or context.get("agent_name") or "").strip()


def resolve_sandbox_id(context: ToolExecutionContextService) -> str:
    """Return the calling agent's sandbox id, or ``""`` when unbound."""
    return str(context.get("sandbox_id") or "").strip()


__all__ = ["resolve_agent_id", "resolve_sandbox_id"]
