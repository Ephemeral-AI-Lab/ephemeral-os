"""Shared generator submission prompt guidance."""

from __future__ import annotations

from tools._names import (
    SUBMIT_GENERATOR_FAILURE_TOOL_NAME,
    SUBMIT_GENERATOR_SUCCESS_TOOL_NAME,
    SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME,
)

GENERATOR_SUBMISSION_CHOICE_GUIDANCE = f"""\
## Success vs Failure vs Handoff Decision

Generator task:
- Treat `<dependencies>` outcomes as context inputs for your `<assigned_task>`.
- Work on the assigned generator task, then choose exactly one terminal tool.

Use `{SUBMIT_GENERATOR_SUCCESS_TOOL_NAME}` when:
- You completed the assigned task and the deliverable is in place.
- Required verification passed, or the task did not require verification.
- Your `outcome` and `artifacts` identify what downstream tasks or reducers
  should read.

Use `{SUBMIT_GENERATOR_FAILURE_TOOL_NAME}` when:
- You attempted the assigned task but cannot complete it in this attempt.
- The blocker is concrete enough for retry or replanning.
- This is an execution failure, not a decomposition request.

Use `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` when:
- The tool is available, you have not started edits, and the task is too broad
  or complex for one executor pass.
- `goal_handoff` gives the planner a self-contained delegated goal, your
  findings, and why decomposition is needed.

Do not use handoff after editing has started. After edits, finish what you can
and choose success or failure from the resulting task state.
"""

__all__ = ["GENERATOR_SUBMISSION_CHOICE_GUIDANCE"]
