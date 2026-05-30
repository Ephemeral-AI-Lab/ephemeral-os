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

A plan is a DAG of generator + reducer tasks (edges are `needs`). Generators
do the work; reducers digest their `needs` and gate the result.

Inputs (this iteration's plan):
- `tasks`: ordered list of generator task descriptors for THIS iteration. ≥ 1
  entry. Each entry is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor agent dispatchable by the
      planner.
    - `needs`: list of other task `id`s that must complete first (default
      `[]`). Cycles and unknown `needs` are rejected.
- `task_specs`: map of generator `task.id` → detailed spec text. Every
  generator `id` must appear; no extras allowed. Each spec is nonblank.
- `reducers`: list of reducer (exit-gate) descriptors. ≥ 1 entry. Each is an
  object with `id` (nonblank), `needs` (default `[]`), and `prompt` (the
  reducer's gating instruction, nonblank). Every generator must be
  transitively needed by at least one reducer.

Input (next iteration's seed):
- `deferred_goal_for_next_iteration`: prose describing the bounded
  remainder. Nonblank. Once THIS iteration's reducers pass, the orchestrator
  spawns a new iteration seeded with this string as its goal.

Behavior:
- Records the plan with `closes_goal=False`. Once the reducers pass, the next
  iteration is spawned automatically from `deferred_goal_for_next_iteration`.\
"""
