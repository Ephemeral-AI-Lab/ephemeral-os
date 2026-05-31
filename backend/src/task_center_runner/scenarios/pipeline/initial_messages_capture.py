"""Scenario that exercises the complex composer paths whose initial
messages the matching report (``docs/reports/initial_messages_report.md``)
captures. Pre-Round-3 this captured exactly three rows per main agent;
Round 3 grew planners to four rows (row 4 = skill composite), so the
name was generalized to "initial messages."

Combines three orthogonal composer branches into one live run so a single
``message.jsonl`` tree carries every variant we want to inspect:

1. **Attempt retry** — iteration 1 attempt 1's planner submits a valid full
   plan, the executor runs the assigned task, and the reducer returns
   ``submit_reducer_outcome``. Attempt 2 then sees a fully-populated
   ``<iteration position="current">`` / ``<attempt attempt_no="1">`` block in
   its planner context: per-task ``<task id status>`` outcomes and a
   ``<failure>`` line. Attempt 2 then submits a partial plan (handoff) to drive
   the continuation branch (#2 below).

2. **Continuation goal** — iteration 1 attempt 2 submits a *partial* plan
   with a ``deferred_goal_for_next_iteration``. The iteration coordinator spawns
   iteration 2 with ``creation_reason=DEFERRED_GOAL_CONTINUATION``; iteration 2's
   planner sees a ``<iteration iteration_no="1" position="prior">`` group whose
   ``<task id status>`` children are the prior iteration's achieved record.

3. **Executor launch coverage** — both iterations include at least one
   generator task per attempt. The single-task plans keep executor captures
   focused on the composer's context shape rather than tool chatter.

This scenario does NOT trigger advisor / subagent calls because its scenario
script does not currently invoke them — those initial-message captures are
produced programmatically by
``scripts/build_initial_messages_report.py``, which calls the real
builder functions in ``tools/ask_helper/_lib/_compose.py`` and
``tools/subagent/explorer_guidance.py`` (specifically
``build_explorer_launch_prompt`` for the subagent's row-2 prose).
Adding a helper/subagent branch to this scenario's event-source script is left
as a follow-up; the matching scenario hook is the ``call_helpers_in_executor``
flag below.

Wire shape (see ``docs/reports/initial_messages_cases/README.md``):

* system + ``<context>`` envelope + ``<Task Guidance>`` envelope + skill
  row for planner / executor / reducer launches (4 rows each — skills
  carry operational heuristics; ``<Task Guidance>`` carries the
  deterministic outline + role directive).
"""

from __future__ import annotations

from collections.abc import Sequence

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from task_center_runner.scenarios._scenario_helpers import (
    preflight_full_plan,
    preflight_defers_plan,
)
from task_center_runner.scenarios.base import (
    ScenarioBase,
    ScenarioContext,
    ToolCallSpec,
)


_CONTINUATION_GOAL = (
    "Continue the initial-messages capture by running one more preflight "
    "in iteration 2 so the continuation planner sees prior iteration "
    "results."
)


class InitialMessagesCapture(ScenarioBase):
    """Continuation + attempt retry, single executor task per attempt.

    Iteration 1, attempt 1: planner submits a *valid* full plan; executor
    runs preflight; reducer returns ``submit_reducer_outcome`` so the
    attempt is closed FAILED with rich, fully-rendered retry evidence.
    Iteration 1, attempt 2: planner sees that retry evidence in a
    ``<attempt attempt_no="1">`` block, submits a partial
    plan with a ``deferred_goal_for_next_iteration``; executor runs
    preflight; reducer passes.
    Iteration 2, attempt 1: planner submits a full plan; executor runs
    preflight; reducer passes; workflow closes succeeded.
    """

    name = "pipeline.initial_messages_capture"

    # Bonus knob a future event-source script branch can read to invoke
    # ask_advisor / run_subagent inline from the executor task. Today it is
    # informational only.
    call_helpers_in_executor: bool = False

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1:
            if ctx.attempt.attempt_sequence_no == 1:
                # Valid full plan — driver for the reducer-failure branch
                # in reducer_response below. Attempt 2's planner will read
                # the resulting `<attempt status="failed">` block.
                return ToolCallSpec(submit_planner_outcome, preflight_full_plan())
            return ToolCallSpec(
                submit_planner_outcome,
                preflight_defers_plan(deferred_goal_for_next_iteration=_CONTINUATION_GOAL),
            )
        return ToolCallSpec(submit_planner_outcome, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1 and ctx.attempt.attempt_sequence_no == 1:
            # Intentional first-attempt reducer failure so the next planner's
            # context carries a fully-populated `<attempt attempt_no="1">`
            # block: real per-task `<task>` outcomes and a `<failure>` line.
            # Without this the retry attempt would only see a bare `<failure>`
            # for the planner-validation failure.
            return ToolCallSpec(
                submit_reducer_outcome,
                {
                    "status": "failed",
                    "outcome": (
                        "Intentional first-attempt reducer failure to "
                        "exercise the rich failed-prior-attempt "
                        "retry-evidence rendering in the next attempt's "
                        "planner context."
                    ),
                },
            )
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "Captured planner / executor / reducer initial messages "
                    f"for iteration {ctx.iteration.sequence_no}."
                ),
            },
        )


__all__ = ["InitialMessagesCapture"]
