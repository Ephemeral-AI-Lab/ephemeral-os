"""Description factory for submit_verification_failure."""

from __future__ import annotations

from tools._names import (
    ASK_RESOLVER_TOOL_NAME,
    SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME,
)


def get_submit_verification_failure_description() -> str:
    return f"""\
Terminate your verifier run with FAILURE for the current generator task.

Call this when:
- One or more checks failed, were skipped, or could not be performed.
- Artifacts the executor claimed to produce are missing or incorrect.

Do NOT call this when:
- Everything passed — use `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}`.
- The issue is fixable inline by a resolver — call `{ASK_RESOLVER_TOOL_NAME}` first
  to attempt a fix, then verify again.

Inputs:
- `summary`: 1–3 sentence recap of what failed.
- `unresolved_issues`: concrete, falsifiable issues. Each entry should
  name what was checked, what was expected, what was observed.

Behavior:
- Records your verifier failure on the attempt. The orchestrator may
  replan or escalate.\
"""
