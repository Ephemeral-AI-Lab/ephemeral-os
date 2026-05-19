"""Description factory for submit_resolver_result."""

from __future__ import annotations

from tools._names import ASK_RESOLVER_TOOL_NAME


def get_submit_resolver_result_description() -> str:
    return f"""\
Terminate the resolver with your outcome.

Call this when:
- You've attempted to address the `issues_to_resolve` passed to you via
  `{ASK_RESOLVER_TOOL_NAME}` and you're ready to report back.

Inputs (current schema):
- `resolved`: True if every issue is addressed; False otherwise.
- `summary`: 1–3 sentence recap of what you changed and why.
- `changed_files`: list of files you modified.
- `remaining_issues`: issues you could NOT resolve (empty when
  `resolved=True`).

Case: every issue addressed
- Set `resolved=True`.
- `summary` lists the fixes by issue.
- `changed_files` lists the edited paths.
- `remaining_issues` is empty.
- Use when each issue from `issues_to_resolve` has been demonstrably
  fixed AND you have evidence (commands run, files inspected).

Case: some issues addressed, others remain
- Set `resolved=False`.
- `summary` explains what you fixed AND what you couldn't, in order.
- `changed_files` lists the edited paths.
- `remaining_issues` lists each unfixed issue verbatim with a one-line
  reason ("requires a planning pass", "blocked by missing dep",
  "ambiguous: needs caller decision").
- Use when partial progress is real and the caller can decide whether
  to re-ask or escalate.

Case: nothing could be addressed
- Set `resolved=False`.
- `summary` states why no fixes were applied (e.g., issues were
  contradictory, scope unclear, blocked by environment).
- `changed_files` may be empty.
- `remaining_issues` echoes the inbound issues.
- Use when the right answer is "the caller needs to clarify or
  escalate", not "try harder inside the resolver".

Do NOT:
- Set `resolved=True` while leaving `remaining_issues` non-empty —
  that's a contradiction.
- Submit with no summary, no changed files, and no remaining issues —
  the caller has no signal to act on.

Behavior:
- The summary is returned to the caller via `{ASK_RESOLVER_TOOL_NAME}`'s result.
  The caller decides whether to re-verify, re-ask, or escalate based
  on the `resolved` flag plus `remaining_issues`.\
"""
