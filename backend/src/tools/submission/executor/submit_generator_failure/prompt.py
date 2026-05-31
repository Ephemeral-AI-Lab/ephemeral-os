"""Description factory for submit_generator_failure."""

from __future__ import annotations

from tools._names import (
    SUBMIT_GENERATOR_SUCCESS_TOOL_NAME,
    SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME,
)
from tools.submission.executor._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_generator_failure_description() -> str:
    return f"""\
Terminate your generator run with FAILED for the current generator task.

## Use This Tool When
- You attempted the task but cannot complete it in this attempt.
- The failure is specific enough for the next planner to understand.

## Do Not Use This Tool When
- You have not actually attempted the task — try first.
- The task is solvable but needs decomposition — use
  `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` instead.
- You succeeded — use `{SUBMIT_GENERATOR_SUCCESS_TOOL_NAME}`.

## Inputs
- `outcome`: 1–3 sentence factual recap of what failed and the evidence.

## Behavior
- Marks this generator task failed. Downstream pending tasks remain
  not-started work that cannot become ready in this attempt.

{GENERATOR_SUBMISSION_CHOICE_GUIDANCE}\
"""
