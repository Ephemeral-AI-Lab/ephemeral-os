"""Shared generator submission prompt guidance."""

from __future__ import annotations

from tools._names import (
    DELEGATE_WORKFLOW_TOOL_NAME,
    SUBMIT_GENERATOR_OUTCOME_TOOL_NAME,
)

GENERATOR_SUBMISSION_CHOICE_GUIDANCE = f"""\
## Success vs Failure Decision

Generator task:
- Treat `<dependencies>` outcomes as context inputs for your `<assigned_task>`.
- Work on the assigned generator task, use `{DELEGATE_WORKFLOW_TOOL_NAME}` only
  when a subtask needs delegated decomposition, then choose exactly one terminal
  tool after all delegated work is resolved.

Call `{SUBMIT_GENERATOR_OUTCOME_TOOL_NAME}` with `status="success"` when:
- You completed the assigned task and the deliverable is in place.
- Required verification passed, or the task did not require verification.
- Your `outcome` identifies what downstream tasks or reducers should read,
  including verification and artifact references.

Call `{SUBMIT_GENERATOR_OUTCOME_TOOL_NAME}` with `status="failed"` when:
- You attempted the assigned task but cannot complete it in this attempt.
- The blocker is concrete enough for retry or replanning.
- Delegated workflow results still leave the assigned task incomplete.
"""

__all__ = ["GENERATOR_SUBMISSION_CHOICE_GUIDANCE"]
