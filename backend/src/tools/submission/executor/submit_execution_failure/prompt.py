"""Description factory for submit_execution_failure."""

from __future__ import annotations

from tools._names import (
    SUBMIT_EXECUTION_HANDOFF_TOOL_NAME,
    SUBMIT_EXECUTION_SUCCESS_TOOL_NAME,
)


def get_submit_execution_failure_description() -> str:
    return f"""\
Terminate your executor run with FAILURE for the current generator task.

Call this when:
- You attempted the task but cannot complete it (environmental block,
  contradictory constraints, missing dependency you can't supply).
- You've made enough attempts that further retries inside this run are
  unlikely to succeed.

Do NOT call this when:
- You haven't actually attempted the task — try first.
- The task is solvable but needs delegation or replanning — use
  `{SUBMIT_EXECUTION_HANDOFF_TOOL_NAME}` instead.
- You succeeded — use `{SUBMIT_EXECUTION_SUCCESS_TOOL_NAME}`.

Inputs:
- `summary`: 1–3 sentence factual recap of what blocked you.
- `reason`: short category-like label ("env", "missing_dependency",
  "contradictory_spec", "out_of_scope").
- `details`: bullet list of concrete evidence (command outputs, file
  paths, symptoms) that justifies the failure.

Behavior:
- Records evaluator-visible failure. The orchestrator may replan or
  escalate.\
"""
