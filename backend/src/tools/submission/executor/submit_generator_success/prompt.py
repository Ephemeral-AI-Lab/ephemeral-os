"""Description factory for submit_generator_success."""

from __future__ import annotations

from tools._names import (
    SUBMIT_GENERATOR_FAILURE_TOOL_NAME,
    SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME,
)
from tools.submission.executor._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_generator_success_description() -> str:
    return f"""\
Terminate your generator run with SUCCESS for the current generator task.

## Use This Tool When
- You've completed the assigned task and the deliverable is in place.
- You can list the concrete artifacts you produced.

## Do Not Use This Tool When
- Any acceptance criterion is unmet — use `{SUBMIT_GENERATOR_FAILURE_TOOL_NAME}`.
- The task is beyond your scope or too complex to solve in one shot —
  use `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` to delegate to the planner.
- You haven't actually performed the work yet.

## Inputs
- `outcome`: 1–3 sentence factual recap of what you did. No filler.
- `artifacts`: list of concrete artifacts (file paths, command IDs) the
  caller can verify.

## Behavior
- Records reducer-visible success on the generator task. The orchestrator
  advances the DAG.

{GENERATOR_SUBMISSION_CHOICE_GUIDANCE}\
"""
