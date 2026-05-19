"""Description factory for submit_evaluation_failure."""

from __future__ import annotations


def get_submit_evaluation_failure_description() -> str:
    return """\
Terminate your evaluator run with FAILURE for the current attempt.

Call this when:
- One or more evaluation criteria are not met.
- The attempt's artifacts do not satisfy the plan's acceptance bar.

Inputs:
- `summary`: 1–3 sentence recap, citing specific gaps.
- `failed_criteria`: list of criteria that did not pass (echo from the
  plan; do not invent new ones).

Behavior:
- Records your evaluator failure on the attempt. The orchestrator may
  replan or spawn a follow-up iteration.\
"""
