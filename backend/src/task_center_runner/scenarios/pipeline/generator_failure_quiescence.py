"""Generator failure → dispatcher waits for in-flight siblings → retry trial.

Plan shape per trial::

       a   b   c        (three parallel root tasks)
        \\  |  /
            d           (depends on a, b, c)

On trial 1, task ``b`` calls ``submit_execution_failure``. The dispatcher
**does not** abort the trial immediately — quiescence semantics require it
to wait for the still-running siblings ``a`` and ``c`` to finish before
closing the trial. Once all three reach a terminal state (``a`` DONE,
``b`` FAILED, ``c`` DONE), ``d`` is marked BLOCKED (it depended on the
failed task). Quiescence is reached → the trial closes
``status=failed``, ``fail_reason="generator_failed"``.

Iteration budget permits a second trial. Trial 2 runs the same plan but
the executor reads ``ctx.trial.trial_sequence_no == 2`` and skips the
failure injection — all four tasks run ``preflight`` and pass; evaluator
accepts; goal closes succeeded.

Asserts: 1 goal (succeeded), 1 iteration, 2 trials; trial 1 contains
exactly three EXECUTOR_INVOKED events (a, b, c) and one EXECUTOR_FAILURE
(b); ``d`` was NOT executed in trial 1; trial 2 contains four
EXECUTOR_SUCCESS events.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.evaluator import submit_evaluation_success
from tools.submission.planner import submit_full_plan

from task_center_runner.audit.events import EventType
from task_center_runner.scenarios._utils import field as _field
from task_center_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FAIL_TAG = "ACTION fail_on_attempt=1"


def _three_plus_one_plan() -> dict[str, Any]:
    return {
        "task_specification": (
            "Three parallel preflight roots feeding into a final preflight."
        ),
        "evaluation_criteria": [
            "All four preflight nodes completed in the passing trial.",
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
    # Trial 1: 3 sibling executor pairs (a/c emit success, b emits failure)
    # then trial closes; planner re-invoked for trial 2.
    # Trial 2: 4 executor pairs all success, evaluator success.
    # Sibling order within an trial is non-deterministic; the test asserts
    # on event-type multisets rather than positional equality.
    expected_event_sequence: tuple[EventType, ...] = (
        EventType.ENTRY_EXECUTOR_INVOKED,
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        # Trial 1 sibling executor events interleave. The stable signal is
        # the injected generator failure before the retry planner invocation.
        EventType.EXECUTOR_FAILURE,
        # Trial 2 — fresh planner, all four nodes succeed, evaluator passes.
        EventType.PLANNER_INVOKED,
        EventType.PLANNER_FULL_PLAN,
        EventType.EXECUTOR_INVOKED,
        EventType.EXECUTOR_SUCCESS,
        EventType.EVALUATOR_INVOKED,
        EventType.EVALUATOR_SUCCESS,
    )

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_full_plan, _three_plus_one_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        rendered_prompt = ctx.rendered_prompt or ctx.prompt or ""
        if _FAIL_TAG in rendered_prompt and ctx.trial.trial_sequence_no == 1:
            tag = _field(rendered_prompt, "tag") or "quiescence"
            return (
                f"fail:Intentional generator failure on trial 1 ({tag}).",
            )
        return ("preflight",)

    def evaluator_response(self, ctx: ScenarioContext) -> ToolCallSpec:
        return ToolCallSpec(
            submit_evaluation_success,
            {
                "summary": (
                    "All four preflight nodes passed on the retry trial; "
                    "quiescence behaviour exercised on trial 1."
                ),
                "passed_criteria": list(ctx.trial.evaluation_criteria),
            },
        )


__all__ = ["GeneratorFailureQuiescence"]
