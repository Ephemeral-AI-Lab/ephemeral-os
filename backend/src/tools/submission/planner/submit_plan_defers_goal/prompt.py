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

Continuation contract:
- The submitted plan must stand on its own. Its tasks and reducers deliver a
  finished slice that closes the current iteration. The continuation is for
  additional work, not unfinished work in this graph.
- `deferred_goal_for_next_iteration` is the next iteration's whole scope, not
  a backlog dump or a diff against this attempt. Write it as a self-contained
  instruction for a fresh planner.
- If the remainder contains many independent items, choose one coherent,
  bounded next slice and leave later remainder for that future planner to size.

A plan is a DAG of generator + reducer tasks (edges are `needs`). Generators
do the work; reducers digest their `needs` and gate the result.

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

Inputs (this iteration's plan):
- `tasks`: ordered list of generator task descriptors for THIS iteration. ≥ 1
  entry. Each entry is an object with:
    - `id`: short unique identifier (nonblank).
    - `agent_name`: name of a registered executor agent dispatchable by the
      planner.
    - `needs`: list of generator `id`s that must complete first (default
      `[]`). Cycles, unknown `needs`, and reducer dependencies are rejected.
- `task_specs`: map of generator `task.id` → detailed spec text. Every
  generator `id` must appear; no extras allowed. Each spec is nonblank.
- `reducers`: list of reducer (exit-gate) descriptors. ≥ 1 entry. Each is an
  object with `id` (nonblank), `needs`, and `prompt` (the
  reducer's gating instruction, nonblank). `needs` must contain one or more
  generator ids. Every generator must have at least one downstream generator or reducer
  consumer (dangling generators are rejected).

Input (next iteration's seed):
- `deferred_goal_for_next_iteration`: prose describing the bounded
  remainder. Nonblank. Once THIS iteration's reducers pass, the orchestrator
  spawns a new iteration seeded with this string as its goal.

Behavior:
- Records the deferring plan. Once the reducers pass, the next iteration is
  spawned automatically from `deferred_goal_for_next_iteration`.\
"""
