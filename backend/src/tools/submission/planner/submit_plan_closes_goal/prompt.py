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

Plan shape:
- Root generators may have no `needs`.
- Non-root generator `needs` may reference one or more generator ids.
- Reducer `needs` must reference one or more generator ids.
- No task may need a reducer; reducers are terminal sinks.
- Every generator must be needed by another generator or by a reducer.
- `needs` are direct context inputs, not just ordering edges. A task sees only
  its direct `needs` outcomes; transitive ancestors are not automatically
  included. If `gen_b` needs `gen_a` and `gen_c` needs both outputs, set
  `gen_c.needs = ["gen_a", "gen_b"]`.

Patterns:

Overview graph:
   gen_a ----\\
              +--> gen_c ----\\
   gen_b ----/                +--> gen_e ----\\
             \\                /               +--> red_f
              +--> gen_d ----+--------------/
   gen_c -----------------------------------> red_g

1. One full serial lane:
   gen_a -> gen_b -> gen_c -> red_d

2. Multiple serial lanes:
   gen_a -> gen_b -> red_e
   gen_c -> gen_d -> red_f

3. Parallel workers with two reducer lanes:
   gen_a ----\\
   gen_b -----+--> gen_d ----\\
   gen_c ----/                +--> red_f
   gen_a ----\\                /
   gen_c -----+------------> red_e

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
