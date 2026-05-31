"""Description factory for submit_generator_outcome."""

from __future__ import annotations

from tools._names import SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME
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
- The task is too broad or genuinely needs planner decomposition before edits
  begin; use `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` when that handoff path is
  available.

## Behavior
- Records reducer-visible generator success or failure on the current task.
- The orchestrator advances the DAG after the submission is accepted.

{GENERATOR_SUBMISSION_CHOICE_GUIDANCE}\
"""


__all__ = ["get_submit_generator_outcome_description"]
