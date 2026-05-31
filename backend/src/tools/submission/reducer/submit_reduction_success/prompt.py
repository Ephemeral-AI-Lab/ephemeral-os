"""Description factory for submit_reduction_success."""

from __future__ import annotations

from tools._names import SUBMIT_REDUCTION_FAILURE_TOOL_NAME
from tools.submission.reducer._prompt_guidance import (
    REDUCTION_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_reduction_success_description() -> str:
    return f"""\
Terminate your reducer run with SUCCESS for the current attempt.

## Use This Tool When
- You have finished the work in `<assigned_task>` using the
  `<dependencies>` outcomes as context.
- Your `outcome` summarizes the reducer result that should be carried forward.

## Do Not Use This Tool When
- The `<assigned_task>` is unfinished, blocked, or cannot be completed from
  the current context — use `{SUBMIT_REDUCTION_FAILURE_TOOL_NAME}`.
- You only reviewed the dependency outcomes but did not complete the assigned
  reducer work.

## Inputs
- `outcome`: 1–3 sentence summary of the completed reducer work and the
  outcome/context it produces.

## Behavior
- Records your reducer success on this task. When every plan task is done
  the attempt closes successfully.

{REDUCTION_SUBMISSION_CHOICE_GUIDANCE}\
"""
