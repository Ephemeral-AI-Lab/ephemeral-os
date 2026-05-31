"""Description factory for submit_plan_defers_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME
from tools.submission.planner._prompt_guidance import PLAN_DAG_GUIDANCE


def get_submit_plan_defers_goal_description() -> str:
    return f"""\
Submit a plan that delivers a bounded slice of the goal in this iteration
and defers the remainder to a follow-up iteration.

## When to Use This Tool
- The full goal is too large or risky to complete safely in one
  iteration.
- You can articulate a bounded slice that is independently valuable AND
  a clear `deferred_goal_for_next_iteration` describing what's left.

## When NOT to Use This Tool
- The full goal fits in one iteration — use `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}`.
- You haven't decided what to defer — that's a planning signal, not a
  slicing one.

## Decision Reasoning
- Use deferral only when this graph can close a valuable slice AND the
  remainder is a coherent next-iteration goal.
- The deferred goal must be the next planner's whole goal, not leftover notes
  or conditions on this plan.
- Do not defer because the plan is uncertain, reducers are too broad, or some
  current-slice work is unfinished. If the slice boundary is unclear, keep
  planning instead of submitting.
- If this iteration can gate every requirement, use
  `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}`.

## Examples
- Do NOT defer when the current goal is fully satisfied by a
  phase_a -> phase_b -> phase_c -> ... lane. A plan like
  `gen_phase_a -> gen_phase_b -> gen_phase_c -> ... -> red_final` should use
  `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}` if `red_final` gates the complete result
  the goal asked for.
- Defer when the current goal needs more than one iteration and this attempt
  can close only the first bounded slice: `gen_phase_a -> gen_phase_b ->
  gen_phase_c -> ...` produces a concrete next-iteration plan, not the final
  result. Gate that plan here, then set `deferred_goal_for_next_iteration` to a
  self-contained goal like "Use the plan from this iteration to implement the
  next slice and run the relevant checks."
- Do NOT defer with vague remainder such as "replan later" or "continue the
  rest." Keep planning until the next iteration's goal is specific enough for a
  fresh planner to execute.

## Continuation Contract
- The submitted plan must stand on its own. Its tasks and reducers deliver a
  finished slice that closes the current iteration. The continuation is for
  additional work, not unfinished work in this graph.
- `deferred_goal_for_next_iteration` is the next iteration's whole scope, not
  a backlog dump or a diff against this attempt. Write it as a self-contained
  instruction for a fresh planner.
- If the remainder contains many independent items, choose one coherent,
  bounded next slice and leave later remainder for that future planner to size.

{PLAN_DAG_GUIDANCE}

## Inputs
- `tasks`: one or more generator descriptors for THIS iteration. Each has `id`,
  `agent_name`, and `needs`. Use `executor` for `agent_name`. `needs` defaults
  to `[]`.
- `task_specs`: map of generator id to detailed, nonblank task spec. It must
  contain exactly the generator ids from `tasks`.
- `reducers`: one or more reducer descriptors. Each has `id`, nonempty `needs`,
  and nonblank `prompt`.
- `deferred_goal_for_next_iteration`: self-contained, nonblank instruction for
  the next iteration's bounded remainder.

Validation rejects cycles, unknown ids, reducer dependencies, reducers with no
generator inputs, extra or missing `task_specs`, and dangling generators with no
downstream generator or reducer consumer.

## Behavior
- Records the deferring plan. Once the reducers pass, the next iteration is
  spawned automatically from `deferred_goal_for_next_iteration`.\
"""
