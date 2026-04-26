"""Shared helpers for Daytona tools that mutate files."""

from __future__ import annotations

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.ci_adapter import ci_write_required_result, get_ci_service


def ci_write_guard(
    context: ToolExecutionContextService,
    *,
    tool_name: str,
    path: str,
) -> ToolResult | None:
    """Return the standard CI-required error when writes are unavailable."""
    if get_ci_service(context) is None:
        return ci_write_required_result(tool_name, path)
    return None


__all__ = ["ci_write_guard"]
