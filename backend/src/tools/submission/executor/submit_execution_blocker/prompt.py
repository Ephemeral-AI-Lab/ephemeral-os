"""Description factory for submit_execution_blocker."""

from __future__ import annotations

from tools._names import (
    SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME,
    SUBMIT_EXECUTION_SUCCESS_TOOL_NAME,
)


def get_submit_execution_blocker_description() -> str:
    return f"""\
Terminate your executor run with BLOCKER for the current generator task.

Call this when:
- You attempted the task but cannot proceed because of a concrete blocker
  (environmental block, contradictory constraints, missing dependency you
  cannot supply).
- The blocker is specific enough for the attempt failure handler and next
  planner to understand.

Do NOT call this when:
- You have not actually attempted the task — try first.
- The task is solvable but needs delegation or replanning — use
  `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` instead.
- You succeeded — use `{SUBMIT_EXECUTION_SUCCESS_TOOL_NAME}`.

Inputs:
- `summary`: 1–3 sentence factual recap of what blocked you and the
  evidence for it.

Behavior:
- Marks this generator task BLOCKED. Downstream tasks remain PENDING as
  not-started work that cannot become ready in this attempt.\
"""
