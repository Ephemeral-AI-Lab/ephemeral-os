"""Description factory for submit_reduction_success."""

from __future__ import annotations

from tools._names import SUBMIT_REDUCTION_FAILURE_TOOL_NAME


def get_submit_reduction_success_description() -> str:
    return f"""\
Terminate your reducer run with SUCCESS for the current attempt.

Call this when:
- The `<needs>` outcomes satisfy your `<assigned_prompt>`.
- The slice you gate meets its acceptance bar.

Do NOT call this when:
- The `<assigned_prompt>` is not satisfied — use `{SUBMIT_REDUCTION_FAILURE_TOOL_NAME}`.
- You haven't actually checked the `<needs>` outcomes against your
  `<assigned_prompt>` — do that first.

Inputs:
- `outcome`: 1–3 sentence recap of the reduction outcome.

Behavior:
- Records your reducer pass on this task. When every plan task is done
  the attempt closes successfully.\
"""
