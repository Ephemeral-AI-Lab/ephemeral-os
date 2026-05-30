"""Description factory for submit_plan_closes_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME


def get_submit_plan_closes_goal_description() -> str:
    return f"""\
Submit a plan that closes the goal once its reducers PASS (one bounded
iteration, no continuation).

Call this when:
- The goal can be fully delivered within this iteration — no follow-on
  slice is needed.
- Your reducers gate every requirement; once they pass, the goal is done.

Do NOT call this when:
- The goal is too large or risky for one iteration — use
  `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}` and articulate the next-iteration slice.
- You haven't decomposed into tasks yet — planning isn't done.

A plan is a DAG of generator + reducer tasks (edges are `needs`). Generators
do the work; reducers digest their `needs` and gate the result. The attempt
PASSES iff every plan task reaches DONE.

Inputs:
- `tasks`: ordered list of generator task descriptors. ≥ 1 entry. Each entry
  is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor agent dispatchable by the
      planner.
    - `needs`: list of other task `id`s (generator or reducer) that must
      complete first (default `[]`). Cycles and unknown `needs` are rejected.
- `task_specs`: map of generator `task.id` → detailed spec text. Every
  generator `id` must appear; no extras allowed. Each spec is nonblank.
- `reducers`: list of reducer (exit-gate) descriptors. ≥ 1 entry. Each is an
  object with:
    - `id`: short unique identifier (nonblank).
    - `needs`: list of task `id`s the reducer digests/gates (default `[]`).
    - `prompt`: the reducer's gating instruction (nonblank).
  Every generator must be transitively needed by at least one reducer
  (unreachable generators are rejected).

Behavior:
- Records the plan and instantiates the generator + reducer DAG; the single
  RUN stage schedules it to quiescence.\
"""
