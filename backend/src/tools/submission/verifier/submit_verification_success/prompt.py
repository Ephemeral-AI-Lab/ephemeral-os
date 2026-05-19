"""Description factory for submit_verification_success."""

from __future__ import annotations

from tools._names import SUBMIT_VERIFICATION_FAILURE_TOOL_NAME


def get_submit_verification_success_description() -> str:
    return f"""\
Terminate your verifier run with SUCCESS for the current generator task.

Call this when:
- Every check you ran passed.
- The artifacts produced by the executor actually exist and behave as
  specified.

Do NOT call this when:
- Any check failed, was skipped, or is unverifiable — use
  `{SUBMIT_VERIFICATION_FAILURE_TOOL_NAME}` with the unresolved issues.

Inputs:
- `summary`: 1–3 sentence recap of what you verified.
- `checks`: list of the concrete verifications you performed (commands
  run, invariants asserted, files inspected). One entry per check.

Behavior:
- Records your verifier pass on the attempt. The orchestrator advances.\
"""
