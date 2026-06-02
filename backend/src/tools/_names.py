"""Tool-name constants.

Single source of truth for tool names referenced across `_prompt.py`
modules. Keep this file literals-only — no imports — so any tool module
can import these constants without risking circular imports.
"""

from __future__ import annotations

# Workspace tools (backend/src/tools/sandbox/)
EXEC_COMMAND_TOOL_NAME = "exec_command"
READ_FILE_TOOL_NAME = "read_file"
EDIT_FILE_TOOL_NAME = "edit_file"
MULTI_EDIT_TOOL_NAME = "multi_edit"
WRITE_FILE_TOOL_NAME = "write_file"
GREP_TOOL_NAME = "grep"
GLOB_TOOL_NAME = "glob"

# Subagent
RUN_SUBAGENT_TOOL_NAME = "run_subagent"
CHECK_SUBAGENT_PROGRESS_TOOL_NAME = "check_subagent_progress"
CANCEL_SUBAGENT_TOOL_NAME = "cancel_subagent"

# Helper-ask tools (backend/src/tools/ask_helper/)
ASK_ADVISOR_TOOL_NAME = "ask_advisor"

# Workflow tools
DELEGATE_WORKFLOW_TOOL_NAME = "delegate_workflow"
CHECK_WORKFLOW_STATUS_TOOL_NAME = "check_workflow_status"
CANCEL_WORKFLOW_TOOL_NAME = "cancel_workflow"

# Generator terminal tools
SUBMIT_GENERATOR_OUTCOME_TOOL_NAME = "submit_generator_outcome"

# Root terminal tools
SUBMIT_ROOT_OUTCOME_TOOL_NAME = "submit_root_outcome"

# Reducer terminal tools
SUBMIT_REDUCER_OUTCOME_TOOL_NAME = "submit_reducer_outcome"

# Planner terminal tools
SUBMIT_PLANNER_OUTCOME_TOOL_NAME = "submit_planner_outcome"

# Advisor terminal tool
SUBMIT_ADVISOR_FEEDBACK_TOOL_NAME = "submit_advisor_feedback"

# Explorer terminal tool
SUBMIT_EXPLORATION_RESULT_TOOL_NAME = "submit_exploration_result"


__all__ = [
    "EXEC_COMMAND_TOOL_NAME",
    "READ_FILE_TOOL_NAME",
    "EDIT_FILE_TOOL_NAME",
    "MULTI_EDIT_TOOL_NAME",
    "WRITE_FILE_TOOL_NAME",
    "GREP_TOOL_NAME",
    "GLOB_TOOL_NAME",
    "RUN_SUBAGENT_TOOL_NAME",
    "CHECK_SUBAGENT_PROGRESS_TOOL_NAME",
    "CANCEL_SUBAGENT_TOOL_NAME",
    "ASK_ADVISOR_TOOL_NAME",
    "DELEGATE_WORKFLOW_TOOL_NAME",
    "CHECK_WORKFLOW_STATUS_TOOL_NAME",
    "CANCEL_WORKFLOW_TOOL_NAME",
    "SUBMIT_GENERATOR_OUTCOME_TOOL_NAME",
    "SUBMIT_ROOT_OUTCOME_TOOL_NAME",
    "SUBMIT_REDUCER_OUTCOME_TOOL_NAME",
    "SUBMIT_PLANNER_OUTCOME_TOOL_NAME",
    "SUBMIT_ADVISOR_FEEDBACK_TOOL_NAME",
    "SUBMIT_EXPLORATION_RESULT_TOOL_NAME",
]
