"""Description factory for submit_reducer_outcome."""

from __future__ import annotations

from tools.submission.reducer._prompt_guidance import (
    REDUCTION_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_reducer_outcome_description() -> str:
    return f"""\
Terminate your reducer run with SUCCESS or FAILED for the current reducer task.

## Inputs
- `status`: `"success"` when the assigned reducer work is complete, or
  `"failed"` when it cannot be completed from the current context.
- `outcome`: 1-3 sentence summary of the completed reducer result or the
  concrete blocker/missing context.

## Behavior
- Records reducer success or failure on this task.
- A successful reducer can close the attempt once every plan task is done.
- A failed reducer causes the attempt lifecycle to fail or replan.

{REDUCTION_SUBMISSION_CHOICE_GUIDANCE}\
"""


__all__ = ["get_submit_reducer_outcome_description"]
