"""Shared planner submission prompt guidance."""

from __future__ import annotations

from tools._names import (
    SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME,
    SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME,
)

PLAN_SUBMISSION_CHOICE_GUIDANCE = f"""\
## Close vs Defer Decision

Use `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}` when:
- This iteration's generator work and reducer outcomes are enough to finish the
  current iteration goal.
- "Enough" means the reducer outcomes cover the goal; after they exist, no
  known follow-up planner pass is needed.

Use `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}` when:
- You have a concrete plan for this bounded iteration, and the next useful step
  is another planner pass after this iteration's reducer outcomes exist.
- The current plan is concrete; what would be speculative is planning the full
  goal beyond this iteration before those reducer outcomes exist.
- Once current reducers complete successfully, their outcomes become
  prior-iteration context for the next planner, alongside
  `deferred_goal_for_next_iteration`.
- The deferred goal is the next planner's scope; reducer outcomes are context
  for that planner, not a replacement for the deferred goal.

Do not submit either terminal yet when:
- You cannot state this iteration as a concrete generator/reducer DAG with a
  clear reducer outcome set.
- You cannot state why the collection of reducer outcomes is sufficient for
  either current-iteration goal completion (`{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}`)
  or a completed bounded iteration ready for the next planner
  (`{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}`).

Examples:
- Lane shape does not decide close vs defer. A sequence like
  `gen_a -> gen_b -> gen_c -> ...` can close or defer depending on whether
  another planner pass is needed.
- Use `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}` when the collection of reducer
  outcomes is sufficient for the current iteration goal.
- Use `{SUBMIT_PLAN_DEFERS_GOAL_TOOL_NAME}` when the reducers produce iteration
  outcomes that should become context for the next planner, and
  `deferred_goal_for_next_iteration` gives that planner a self-contained next
  goal.
"""

PLAN_DAG_GUIDANCE = """\
## Plan DAG Contract

A plan is a DAG of generator + reducer tasks. Generators do the work; reducers
work on assigned reducer tasks using their direct `needs` as context, then
report outcome summaries. Use the smallest graph that matches the context flow.

Rules:
- Root generators may have no `needs`.
- Non-root generator `needs` may reference one or more generator ids.
- Reducer `needs` must reference one or more generator ids.
- No task may need a reducer; reducers are terminal sinks.
- Every generator must be needed by another generator or by a reducer.

Context rule:
- `needs` are direct context inputs, not scheduling shortcuts.
- A task receives only the outcomes of ids listed in its own `needs`;
  transitive ancestors are not included.
- If `gen_b` needs `gen_a` and `gen_c` needs `gen_b`, then `gen_c` receives
  `gen_b` only.
- If `gen_c` also needs `gen_a`'s context, set
  `gen_c.needs = ["gen_a", "gen_b"]`.

Valid examples:

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

3. Simple fan-in reducer:
   gen_a ----\\
   gen_b -----+--> red_d
   gen_c ----/

4. Diamond fork-join:
             +--> gen_b ----\\
   gen_a ----+                +--> gen_d ----> red_e
             +--> gen_c ----/

5. Tree:
   gen_a
   +--> gen_b ----\\
   |               +--> red_f
   +--> gen_c ----/
        +--> gen_d ----> red_g
        +--> gen_e ----> red_h

6. Fully-connected layers:
   gen_a ----+--> gen_c ----+--> red_e
             |              |
   gen_b ----+--> gen_d ----+--> red_f

7. Multi-phase mesh:
   gen_a ----> gen_c ----\\
                         +--> gen_e ----\\
   gen_b ----> gen_d ----/                +--> gen_h ----\\
                         +--> gen_f ----/                +--> red_i
   gen_c -----------------------------------------------> red_j
"""

__all__ = ["PLAN_DAG_GUIDANCE", "PLAN_SUBMISSION_CHOICE_GUIDANCE"]
