"""Description factory for submit_execution_handoff."""

from __future__ import annotations

from tools._names import (
    SUBMIT_EXECUTION_BLOCKER_TOOL_NAME,
    SUBMIT_EXECUTION_SUCCESS_TOOL_NAME,
)


def get_submit_execution_handoff_description() -> str:
    return f"""\
Request a delegated complex-task solution for the current generator task.
This terminates your executor run and bounces the task back to the
planner.

Call this when:
- The task is genuinely too complex for a single executor pass (requires
  multi-step planning, fan-out to subagents, or cross-file
  coordination).
- You've assessed the scope before making edits — and have not yet
  edited.
- A cleaner break-up into smaller sub-tasks would produce a more
  reliable outcome.

You MUST call this BEFORE making edits. If you've already started
editing, finish what you can and use `{SUBMIT_EXECUTION_SUCCESS_TOOL_NAME}` or
`{SUBMIT_EXECUTION_BLOCKER_TOOL_NAME}` instead.

Do NOT call this when:
- The task is bounded and doable — just do it.
- You're stuck on an environment issue — that's
  `{SUBMIT_EXECUTION_BLOCKER_TOOL_NAME}`, not a handoff.

Inputs:
- `goal`: the higher-level goal the planner should re-plan against. Be
  specific about what's hard and what shape of decomposition you'd
  suggest.

Behavior:
- Hands the task back to the planner with the proposed goal; spawns a
  fresh planning iteration.\
"""
