"""Description factory for submit_reduction_failure."""

from __future__ import annotations


def get_submit_reduction_failure_description() -> str:
    return """\
Terminate your reducer run with FAILURE for the current attempt.

Call this when:
- The `<needs>` outcomes do not satisfy your `<assigned_prompt>`.
- The slice you gate does not meet its acceptance bar.

Inputs:
- `outcome`: 1–3 sentence recap, citing the specific gap.

Behavior:
- Records your reducer failure on this task. The orchestrator may
  replan or spawn a follow-up iteration.\
"""
