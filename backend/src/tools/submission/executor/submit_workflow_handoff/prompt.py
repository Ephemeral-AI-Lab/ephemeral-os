"""Description factory for submit_workflow_handoff."""

from __future__ import annotations

from tools._names import (
    SUBMIT_GENERATOR_FAILURE_TOOL_NAME,
    SUBMIT_GENERATOR_SUCCESS_TOOL_NAME,
)
from tools.submission.executor._prompt_guidance import (
    GENERATOR_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_workflow_handoff_description() -> str:
    return f"""\
Hand the current task back to the planner for decomposition into smaller
sub-objectives. This terminates your executor run.

## Use This Tool When
- The current objective's scope is too large for a single executor pass and
  would be more reliably completed as several smaller sub-objectives.
- You've assessed the scope BEFORE making edits — and have not yet
  edited.

You MUST call this BEFORE making edits. If you've already started
editing, finish what you can and use `{SUBMIT_GENERATOR_SUCCESS_TOOL_NAME}`
or `{SUBMIT_GENERATOR_FAILURE_TOOL_NAME}` instead.

## Do Not Use This Tool When
- The task is bounded and doable — just do it.
- You're stuck on an environment or dependency issue — that's
  `{SUBMIT_GENERATOR_FAILURE_TOOL_NAME}`, not a decomposition request.

## Inputs
- `goal_handoff`: the original goal statement (verbatim or paraphrased
  without information loss), plus your findings and the reasons it
  needs to be decomposed by the planner.

## Behavior
- Spawns a fresh planning iteration with the handed-off goal as the new
  goal statement.

{GENERATOR_SUBMISSION_CHOICE_GUIDANCE}\
"""
