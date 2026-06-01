"""Hard-coded engine background-manager wiring."""

from __future__ import annotations

from typing import Any

from engine.background.task_supervisor import SUBAGENT_TASK_TYPE

SUBAGENT_LAUNCH_TOOL_NAMES = frozenset({"run_subagent"})
PTY_SESSION_TOOL_NAMES = frozenset(
    {
        "cancel_pty_command",
        "check_pty_command_progress",
        "exec_command",
        "write_pty_command_stdin",
    }
)


def is_engine_background_tool(tool: Any) -> bool:
    """Return whether a tool must launch through BackgroundTaskSupervisor."""
    return (
        getattr(tool, "name", "") in SUBAGENT_LAUNCH_TOOL_NAMES
        or getattr(tool, "task_type", "") == SUBAGENT_TASK_TYPE
    )


def needs_background_manager(tool: Any) -> bool:
    """Return whether this tool surface needs the per-query background manager."""
    return (
        is_engine_background_tool(tool)
        or getattr(tool, "name", "") in PTY_SESSION_TOOL_NAMES
    )
