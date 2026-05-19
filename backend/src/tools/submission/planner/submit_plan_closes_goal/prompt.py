"""Description factory for submit_plan_closes_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME


def get_submit_plan_closes_goal_description() -> str:
    return f"""\
Submit a plan that closes the goal on evaluator PASS (one bounded
iteration, no continuation).

Call this when:
- The goal can be fully delivered within this iteration — no follow-on
  slice is needed.
- Your `evaluation_criteria` cover every requirement; once they pass,
  the goal is done.

Do NOT call this when:
- The goal is too large or risky for one iteration — use
  `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}` and articulate the next-iteration slice.
- You haven't decomposed into tasks yet — planning isn't done.

Inputs:
- `plan_spec`: high-level plan rationale (what, why, scope of this
  iteration). Nonblank.
- `evaluation_criteria`: list of falsifiable acceptance criteria. ≥ 1
  entry, each nonblank.
- `tasks`: ordered list of task descriptors. ≥ 1 entry. Each entry is
  an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor/verifier agent
      dispatchable by the planner.
    - `deps`: list of other task `id`s that must complete first
      (default `[]`). Cycles and unknown deps are rejected.
- `task_specs`: map of `task.id` → detailed spec text. Every task `id`
  must appear; no extras allowed. Each spec is nonblank.

Behavior:
- Records the plan with `closes_goal=True`. The orchestrator
  instantiates the task DAG and runs it.\
"""
