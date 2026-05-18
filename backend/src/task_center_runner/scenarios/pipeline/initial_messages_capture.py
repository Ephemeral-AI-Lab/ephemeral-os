"""Scenario that exercises the complex composer paths whose initial
messages the matching report (``docs/reports/initial_messages_report.md``)
captures. Pre-Round-3 this captured exactly three rows per main agent;
Round 3 grew planners to four rows (row 4 = skill composite), so the
name was generalized to "initial messages."

Combines three orthogonal composer branches into one live run so a single
``message.jsonl`` tree carries every variant we want to inspect:

1. **Attempt retry** — iteration 1 attempt 1 submits a plan with an unknown
   dependency, which the planner-validation guard rejects; attempt 2
   resubmits a valid plan. The retry attempt's planner sees
   ``# Prior Failed Attempts`` evidence.

2. **Continuation goal** — iteration 1 submits a *partial* plan with a
   ``next_iteration_handoff_goal`` once the retry recovers. The iteration manager
   spawns iteration 2 with ``creation_reason=PARTIAL_CONTINUATION``;
   iteration 2's planner sees ``# Previous Iteration Results``.

3. **Different agent routings** — both iterations include at least one
   generator task per attempt, so the `RuleBasedAgentResolver` picks the
   right executor variant for that depth (today: `executor_success_failure`
   when the planner closes the goal, `executor_success_handoff` when a
   handoff sub-goal is in play). The dependency-less single-task plan keeps
   the executor side trivial so message capture isn't bloated by tool
   chatter.

This scenario does NOT trigger advisor / resolver / subagent calls because
the mock runner does not currently invoke them — those initial-message
captures are produced programmatically by
``scripts/build_initial_messages_report.py``, which calls the real
builder functions in ``tools/ask_helper/_lib/_compose.py`` and
``task_center/context_engine/recipes/role_instruction.py``. Adding a
helper/subagent dispatch branch to ``MockSquadRunner`` is left as a
follow-up; the matching scenario hook is the ``call_helpers_in_executor``
flag below, which the runner can grow into later.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import (
    submit_plan_closes_goal,
    submit_plan_continues_goal,
)

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import (
    preflight_full_plan,
    preflight_partial_plan,
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


def _invalid_plan_with_unknown_dep() -> dict[str, Any]:
    return {
        "plan_spec": (
            "Invalid first-attempt plan — references an unknown dependency "
            "so the planner-validation guard rejects it and triggers the "
            "attempt-retry path."
        ),
        "evaluation_criteria": [
            "Planner failure triggers an attempt retry (validation step)."
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": ["missing"]},
        ],
        "task_specs": {"a": "Run a workspace preflight."},
    }


class InitialMessagesCapture(ScenarioBase):
    """Continuation + attempt retry, single executor task per attempt.

    Iteration 1, attempt 1: planner submits an invalid plan → TOOL_CALL_ERROR.
    Iteration 1, attempt 2: planner submits a partial plan with a
    next_iteration_handoff_goal; executor runs preflight; evaluator passes.
    Iteration 2, attempt 1: planner submits a full plan; executor runs
    preflight; evaluator passes; goal closes succeeded.
    """

    name = "pipeline.initial_messages_capture"

    # Bonus knob a future MockSquadRunner extension can read to invoke
    # ask_advisor / ask_resolver / run_subagent inline from the executor
    # task. Today it is informational only.
    call_helpers_in_executor: bool = False

    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        # Iteration 1 attempt 1 — planner fails validation.
        EventType.PLANNER_INVOKED,
        EventType.TOOL_CALL_ERROR,
        # Iteration 1 attempt 2 — planner submits partial plan.
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_PARTIAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
        # Iteration 2 — full plan after continuation.
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        if ctx.iteration.sequence_no == 1:
            if ctx.attempt.attempt_sequence_no == 1:
                return ToolCallSpec(
                    submit_plan_closes_goal, _invalid_plan_with_unknown_dep()
                )
            return ToolCallSpec(
                submit_plan_continues_goal,
                preflight_partial_plan(next_iteration_handoff_goal=_CONTINUATION_GOAL),
            )
        return ToolCallSpec(submit_plan_closes_goal, preflight_full_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:  # noqa: ARG002
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "Captured planner / executor / evaluator initial messages "
                    f"for iteration {ctx.iteration.sequence_no}."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["InitialMessagesCapture"]
