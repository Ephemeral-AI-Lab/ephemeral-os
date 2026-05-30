"""Description factory for submit_execution_success."""

from __future__ import annotations

from tools._names import (
    SUBMIT_EXECUTION_BLOCKER_TOOL_NAME,
    SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME,
)


def get_submit_execution_success_description() -> str:
    return f"""\
Terminate your executor run with SUCCESS for the current generator task.

Call this when:
- You've completed the assigned executor task and the deliverable is in
  place (file created, edits applied, command run with the expected
  effect).
- You can list the concrete artifacts you produced.

Do NOT call this when:
- Any acceptance criterion is unmet — use `{SUBMIT_EXECUTION_BLOCKER_TOOL_NAME}`.
- The task is beyond your scope or too complex to solve in one shot —
  use `{SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME}` to delegate to the planner.
- You haven't actually performed the work yet — terminate only after
  your changes are durable.

Inputs:
- `summary`: 1–3 sentence factual recap of what you did. No filler.
- `artifacts`: list of concrete artifacts (file paths, command IDs) the
  caller can verify.

Behavior:
- Records evaluator-visible success on the attempt's task. The
  orchestrator advances the DAG.\
"""
