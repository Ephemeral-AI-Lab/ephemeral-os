"""Description factory for submit_reduction_failure."""

from __future__ import annotations

from tools.submission.reducer._prompt_guidance import (
    REDUCTION_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_reduction_failure_description() -> str:
    return f"""\
Terminate your reducer run with FAILURE for the current attempt.

## Use This Tool When
- You cannot finish the work in `<assigned_task>` using the current
  `<dependencies>` outcomes as context.
- A dependency outcome is missing, contradictory, insufficient, or otherwise
  blocks completion of the assigned reducer work.

## Inputs
- `outcome`: 1–3 sentence failure report citing the assigned work, the
  specific blocker or missing context, and what a next attempt needs.

## Behavior
- Records your reducer failure on this task. The orchestrator may
  replan or spawn a follow-up iteration.

{REDUCTION_SUBMISSION_CHOICE_GUIDANCE}\
"""
