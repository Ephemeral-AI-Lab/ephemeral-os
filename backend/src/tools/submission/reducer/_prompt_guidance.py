"""Shared reducer submission prompt guidance."""

from __future__ import annotations

from tools._names import (
    SUBMIT_REDUCER_OUTCOME_TOOL_NAME,
)

REDUCTION_SUBMISSION_CHOICE_GUIDANCE = f"""\
## Success vs Failure Decision

Reducer task:
- Treat `<dependencies>` outcomes as context inputs for your `<assigned_task>`.
- Work on the assigned reducer task, then choose exactly one terminal tool.

Call `{SUBMIT_REDUCER_OUTCOME_TOOL_NAME}` with `status="success"` when:
- You finished the assigned reducer work.
- Your `outcome` summarizes what you completed and the reducer outcome/context
  that should be carried forward.

Call `{SUBMIT_REDUCER_OUTCOME_TOOL_NAME}` with `status="failed"` when:
- You cannot finish the assigned reducer work from the current context.
- The dependency outcomes are missing, contradictory, insufficient, or expose a
  blocker that requires another attempt or planner iteration.

Do not submit success just because dependency outcomes look reasonable. Success
means the assigned reducer work is finished; otherwise, submit failure with the
specific blocker or missing context.
"""

__all__ = ["REDUCTION_SUBMISSION_CHOICE_GUIDANCE"]
