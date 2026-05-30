"""Generator failure → task dispatcher waits for in-flight siblings → retry attempt.

Plan shape per attempt::

       a   b   c        (three parallel root tasks)
        \\  |  /
            d           (depends on a, b, c)

On attempt 1, task ``b`` calls ``submit_execution_blocker``. The task dispatcher
**does not** abort the attempt immediately — quiescence semantics require it
to wait for the still-running siblings ``a`` and ``c`` to finish before
closing the attempt. Once all runnable roots reach a terminal state
(``a`` DONE, ``b`` BLOCKED, ``c`` DONE), ``d`` remains PENDING as
not-started work that depended on the blocked task. Quiescence is reached → the attempt closes
``status=failed``, ``fail_reason="generator_failed"``.

Iteration budget permits a second attempt. Attempt 2 runs the same plan but
the executor reads ``ctx.attempt.attempt_sequence_no == 2`` and skips the
failure injection — all four tasks run ``preflight`` and pass; evaluator
accepts; workflow closes succeeded.

Asserts: 1 workflow (succeeded), 1 iteration, 2 attempts; attempt 1 contains
exactly three EXECUTOR_INVOKED events (a, b, c) and one EXECUTOR_FAILURE
(b); ``d`` was NOT executed in attempt 1; attempt 2 contains four
EXECUTOR_SUCCESS events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_plan_closes_goal

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._scenario_helpers import (
    context_message_field as _field,
)
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FAIL_TAG = "ACTION fail_on_attempt=1"


def _three_plus_one_plan() -> dict[str, Any]:
    return {
        "plan_spec": (
            "Three parallel preflight roots feeding into a final preflight."
        ),
        "evaluation_criteria": [
            "All four preflight nodes completed in the passing attempt.",
            "Dispatcher waited for sibling quiescence after the first failure.",
        ],
        "tasks": [
            {"id": "a", "agent_name": "executor", "deps": []},
            {"id": "b", "agent_name": "executor", "deps": []},
            {"id": "c", "agent_name": "executor", "deps": []},
            {"id": "d", "agent_name": "executor", "deps": ["a", "b", "c"]},
        ],
        "task_specs": {
            "a": "Run a workspace preflight (root a).",
            "b": (
                f"Run a workspace preflight (root b). {_FAIL_TAG} "
                "tag=quiescence_b"
            ),
            "c": "Run a workspace preflight (root c).",
            "d": "Run a workspace preflight (final d).",
        },
    }


class GeneratorFailureQuiescence(ScenarioBase):
    """Sibling quiescence on failure → retry passes the same plan cleanly."""

    name = "pipeline.generator_failure_quiescence"
    # Attempt 1: 3 sibling executor pairs (a/c emit success, b emits failure)
    # then attempt closes; planner re-invoked for attempt 2.
    # Attempt 2: 4 executor pairs all success, evaluator success.
    # Sibling order within an attempt is non-deterministic; the test asserts
    # on event-type multisets rather than positional equality.
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        # Attempt 1 sibling executor events interleave. The stable signal is
        # the injected generator failure before the retry planner invocation.
        EventType.EXECUTOR_FAILURE,
        # Attempt 2 — fresh planner, all four nodes succeed, evaluator passes.
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_COMPLETES_GOAL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_plan_closes_goal, _three_plus_one_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        context_message = ctx.context_message or ctx.prompt or ""
        if _FAIL_TAG in context_message and ctx.attempt.attempt_sequence_no == 1:
            tag = _field(context_message, "tag") or "quiescence"
            return (
                f"fail:Intentional generator failure on attempt 1 ({tag}).",
            )
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "All four preflight nodes passed on the retry attempt; "
                    "quiescence behaviour exercised on attempt 1."
                ),
                "passed_criteria": list(ctx.attempt.evaluation_criteria),
            },
        )


__all__ = ["GeneratorFailureQuiescence"]
