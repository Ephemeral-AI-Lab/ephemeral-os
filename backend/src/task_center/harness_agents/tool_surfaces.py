"""Shared tool-surface lists for harness agent definitions."""

from __future__ import annotations

READ_ONLY_INVESTIGATION_TOOLS: list[str] = [
    "grep",
    "glob",
    "read_file",
    "ci_query_symbol",
    "ci_diagnostics",
    "ci_workspace_structure",
]

BACKGROUND_TASK_TOOLS: list[str] = [
    "run_subagent",
    "cancel_background_task",
    "check_background_task_result",
    "wait_background_tasks",
]

PLANNER_TOOLS: list[str] = [
    *READ_ONLY_INVESTIGATION_TOOLS,
    *BACKGROUND_TASK_TOOLS,
]

DIRECT_WORK_TOOLS: list[str] = [
    *READ_ONLY_INVESTIGATION_TOOLS,
    "write_file",
    "edit_file",
    "delete_file",
    "move_file",
    "shell",
    "ci_status",
    *BACKGROUND_TASK_TOOLS,
]

__all__ = [
    "BACKGROUND_TASK_TOOLS",
    "DIRECT_WORK_TOOLS",
    "PLANNER_TOOLS",
    "READ_ONLY_INVESTIGATION_TOOLS",
]
