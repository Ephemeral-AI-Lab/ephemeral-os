"""Description factory for submit_plan_closes_goal."""

from __future__ import annotations

from tools._names import SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME
from tools.submission.planner._prompt_guidance import PLAN_DAG_GUIDANCE


def get_submit_plan_closes_goal_description() -> str:
    return f"""\
Submit a plan that closes the goal once its reducers PASS (one bounded
iteration, no continuation).

## When to Use This Tool
- The goal can be fully delivered within this iteration — no follow-on
  slice is needed.
- Your reducers gate every requirement; once they pass, the goal is done.

## When NOT to Use This Tool
- The goal is too large or risky for one iteration — use
  `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}` and articulate the next-iteration slice.
- You haven't decomposed into tasks yet — planning isn't done.

## Decision Reasoning
- Use close only when you are confident this attempt's generators and reducers
  are sufficient for the whole current goal.
- Sufficient means every required outcome is produced by the DAG and checked by
  reducers; after those reducers PASS, no known follow-up work remains.
- Do not close because the plan might be enough or because later agents might
  discover missing work. If a valuable slice is enough now but more work
  remains, use `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}`.
- If you cannot state why reducer pass equals goal completion, keep planning
  instead of submitting.

{PLAN_DAG_GUIDANCE}

## Completion Rule
The attempt PASSES iff every plan task reaches DONE.

## Inputs
- `tasks`: one or more generator descriptors. Each has `id`, `agent_name`, and
  `needs`. Use `executor` for `agent_name`. `needs` defaults to `[]`.
- `task_specs`: map of generator id to detailed, nonblank task spec. It must
  contain exactly the generator ids from `tasks`.
- `reducers`: one or more reducer descriptors. Each has `id`, nonempty `needs`,
  and nonblank `prompt`.

Validation rejects cycles, unknown ids, reducer dependencies, reducers with no
generator inputs, extra or missing `task_specs`, and dangling generators with no
downstream generator or reducer consumer.

## Behavior
- Records the plan and instantiates the generator + reducer DAG; the single
  RUN stage schedules it to quiescence.\
"""
