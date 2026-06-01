"""Description factory for submit_generator_outcome."""

from __future__ import annotations

from tools._names import DELEGATE_WORKFLOW_TOOL_NAME
from tools.submission.generator._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_generator_outcome_description() -> str:
    return f"""\
Terminate your generator run with SUCCESS or FAILED for the current generator task.

## Inputs
- `status`: `"success"` when the assigned task is complete, or `"failed"`
  when it cannot be completed in this attempt.
- `outcome`: 1-3 sentence factual report. For success, include what changed,
  verification evidence, and artifact references. For failure, include the
  concrete blocker and evidence.

## Do Not Use This Tool When
- A delegated workflow you started is still outstanding; use
  `{DELEGATE_WORKFLOW_TOOL_NAME}` only for new delegated work, then inspect or
  cancel outstanding workflow handles before submitting your final outcome.

## Behavior
- Records reducer-visible generator success or failure on the current task.
- The orchestrator advances the DAG after the submission is accepted.

{GENERATOR_SUBMISSION_CHOICE_GUIDANCE}\
"""


__all__ = ["get_submit_generator_outcome_description"]
