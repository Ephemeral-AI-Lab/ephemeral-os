"""Description factory for submit_planner_outcome."""

from __future__ import annotations

from tools.submission.planner._prompt_guidance import (
    PLAN_DAG_GUIDANCE,
    PLAN_SUBMISSION_CHOICE_GUIDANCE,
)


def get_submit_planner_outcome_description() -> str:
    return f"""\
Terminate your planner run by submitting the generator/reducer DAG for this attempt.

## Inputs
- `tasks`: generator tasks, each with `id`, `agent_name`, and `needs`.
- `task_specs`: mapping from each generator task id to its executable task text.
- `reducers`: reducer tasks, each with `id`, one or more generator `needs`, and `prompt`.
- `deferred_goal_for_next_iteration`: optional concrete goal items from the
  current iteration goal that this plan intentionally leaves for the next
  iteration. Omit or null means this plan covers all current-iteration goal
  items and leaves no remaining items.

## Behavior
- With no deferred goal, the plan closes the current iteration once reducers pass.
- With a nonblank deferred goal, the plan completes this bounded iteration and
  carries those remaining current-iteration goal items into the next iteration.
- The attempt PASSES iff every plan task reaches DONE.

{PLAN_DAG_GUIDANCE}

{PLAN_SUBMISSION_CHOICE_GUIDANCE}\
"""


__all__ = ["get_submit_planner_outcome_description"]
