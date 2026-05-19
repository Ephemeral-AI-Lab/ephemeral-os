"""Description factory for submit_plan_defers_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME


def get_submit_plan_defers_goal_description() -> str:
    return f"""\
Submit a plan that delivers a bounded slice of the goal in this iteration
and defers the remainder to a follow-up iteration.

Call this when:
- The full goal is too large or risky to complete safely in one
  iteration.
- You can articulate a bounded slice that is independently valuable AND
  a clear `deferred_goal_for_next_iteration` describing what's left.

Do NOT call this when:
- The full goal fits in one iteration — use `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}`.
- You haven't decided what to defer — that's a planning signal, not a
  slicing one.

Inputs (this iteration's plan):
- `plan_spec`: high-level rationale for THIS iteration's slice (what,
  why, scope). Nonblank.
- `evaluation_criteria`: list of falsifiable acceptance criteria for
  THIS iteration's slice. ≥ 1 entry, each nonblank.
- `tasks`: ordered list of task descriptors for THIS iteration. ≥ 1
  entry. Each entry is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor/verifier agent
      dispatchable by the planner.
    - `deps`: list of other task `id`s that must complete first
      (default `[]`). Cycles and unknown deps are rejected.
- `task_specs`: map of `task.id` → detailed spec text. Every task `id`
  must appear; no extras allowed. Each spec is nonblank.

Input (next iteration's seed):
- `deferred_goal_for_next_iteration`: prose describing the bounded
  remainder. Nonblank. After THIS iteration's evaluator passes, the
  orchestrator spawns a new iteration seeded with this string as its
  goal.

Behavior:
- Records the plan with `closes_goal=False`. On evaluator PASS, the
  next iteration is spawned automatically from
  `deferred_goal_for_next_iteration`.\
"""
