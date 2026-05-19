"""Description factory for submit_evaluation_success."""

from __future__ import annotations

from tools._names import SUBMIT_EVALUATION_FAILURE_TOOL_NAME


def get_submit_evaluation_success_description() -> str:
    return f"""\
Terminate your evaluator run with SUCCESS for the current attempt.

Call this when:
- Every criterion in the plan's `evaluation_criteria` is satisfied by
  the artifacts produced.
- The attempt as a whole meets its acceptance bar.

Do NOT call this when:
- Any criterion failed — use `{SUBMIT_EVALUATION_FAILURE_TOOL_NAME}`.
- You haven't actually checked the criteria against the artifacts — do
  that first.

Inputs:
- `summary`: 1–3 sentence recap of the evaluation outcome.
- `passed_criteria`: list of criteria the attempt passed (echo from the
  plan; do not invent new ones).

Behavior:
- Records your evaluator pass on the attempt and closes the iteration's
  goal.\
"""
