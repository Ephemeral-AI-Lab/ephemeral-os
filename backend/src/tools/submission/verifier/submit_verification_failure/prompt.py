"""Description factory for submit_verification_failure."""

from __future__ import annotations

from tools._names import (
    EDIT_FILE_TOOL_NAME,
    SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_submit_verification_failure_description() -> str:
    return f"""\
Terminate your verifier run with FAILURE for the current generator task.

Call this when:
- One or more checks failed, were skipped, or could not be performed.
- Artifacts the executor claimed to produce are missing or incorrect.

Do NOT call this when:
- Everything passed — use `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}`.
- The defect is trivial and unambiguous (typo, wrong variable name,
  missing import, off-by-one, formatting, single-line obvious bug) and
  fits in one file — apply the fix inline via `{EDIT_FILE_TOOL_NAME}`
  or `{WRITE_FILE_TOOL_NAME}`, re-run the verification check, and submit
  `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}` if it now passes. Call this
  terminal only when the defect requires understanding intent, touches
  control flow, needs new tests, spans multiple files, or you are not
  confident the fix is correct.

Inputs:
- `summary`: 1–3 sentence recap of what failed.
- `unresolved_issues`: concrete, falsifiable issues. Each entry should
  name what was checked, what was expected, what was observed.

Behavior:
- Records your verifier failure on the attempt. The orchestrator may
  replan or escalate.\
"""
