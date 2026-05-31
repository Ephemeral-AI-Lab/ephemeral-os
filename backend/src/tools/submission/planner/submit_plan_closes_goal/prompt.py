"""Description factory for submit_plan_closes_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME
from tools.submission.planner._prompt_guidance import PLAN_DAG_GUIDANCE


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

{PLAN_DAG_GUIDANCE}
The attempt PASSES iff every plan task reaches DONE.

Inputs:
- `tasks`: ordered list of generator task descriptors. ≥ 1 entry. Each entry
  is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor agent dispatchable by the
      planner.
    - `needs`: list of generator `id`s that must complete first (default
      `[]`). Cycles, unknown `needs`, and reducer dependencies are rejected.
- `task_specs`: map of generator `task.id` → detailed spec text. Every
  generator `id` must appear; no extras allowed. Each spec is nonblank.
- `reducers`: list of reducer (exit-gate) descriptors. ≥ 1 entry. Each is an
  object with:
    - `id`: short unique identifier (nonblank).
    - `needs`: one or more generator `id`s the reducer digests/gates.
    - `prompt`: the reducer's gating instruction (nonblank).
  Every generator must have at least one downstream generator or reducer consumer
  (dangling generators are rejected).

Behavior:
- Records the plan and instantiates the generator + reducer DAG; the single
  RUN stage schedules it to quiescence.\
"""
