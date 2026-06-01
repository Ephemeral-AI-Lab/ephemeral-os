"""Generator failure → task dispatcher waits for in-flight siblings → retry attempt.

Plan shape per attempt::

       a   b   c        (three parallel root tasks)
        \\  |  /
            d           (depends on a, b, c)

On attempt 1, task ``b`` calls ``submit_generator_outcome(status="failed", ...)``. The task dispatcher
**does not** abort the attempt immediately — quiescence semantics require it
to wait for the still-running siblings ``a`` and ``c`` to finish before
closing the attempt. Once all runnable roots reach a terminal state
(``a`` DONE, ``b`` BLOCKED, ``c`` DONE), ``d`` remains PENDING as
not-started work that depended on the blocked task. Quiescence is reached → the attempt closes
``status=failed``, ``fail_reason="task_failed"``.

Iteration budget permits a second attempt. Attempt 2 runs the same plan but
the executor reads ``ctx.attempt.attempt_sequence_no == 2`` and skips the
failure injection — all four tasks run ``preflight`` and pass; reducer
accepts; workflow closes succeeded.

Asserts: 1 workflow (succeeded), 1 iteration, 2 attempts; attempt 1 contains
three executor tasks (a, b, c), one failed executor task (b), and no ``d`` task;
attempt 2 contains four done executor tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tools.submission.planner import submit_planner_outcome
from tools.submission.reducer import submit_reducer_outcome

from test_runner.scenarios._scenario_helpers import (
    instruction_field as _field,
)
from test_runner.scenarios.base import ScenarioBase, ScenarioContext, ToolCallSpec


_FAIL_TAG = "ACTION fail_on_attempt=1"


def _three_plus_one_plan() -> dict[str, Any]:
    return {
        "tasks": [
            {"id": "a", "agent_name": "executor", "needs": []},
            {"id": "b", "agent_name": "executor", "needs": []},
            {"id": "c", "agent_name": "executor", "needs": []},
            {"id": "d", "agent_name": "executor", "needs": ["a", "b", "c"]},
        ],
        "task_specs": {
            "a": "Run a workspace preflight (root a).",
            "b": (f"Run a workspace preflight (root b). {_FAIL_TAG} tag=quiescence_b"),
            "c": "Run a workspace preflight (root c).",
            "d": "Run a workspace preflight (final d).",
        },
        "reducers": [
            {
                "id": "reduce",
                "needs": ["a", "b", "c", "d"],
                "prompt": "Confirm all four preflight nodes passed on the retry attempt.",
            }
        ],
    }


class GeneratorFailureQuiescence(ScenarioBase):
    """Sibling quiescence on failure → retry passes the same plan cleanly."""

    name = "pipeline.generator_failure_quiescence"
    # Attempt 1: 3 sibling executor pairs (a/c emit success, b emits failure)
    # then attempt closes; planner re-invoked for attempt 2.
    # Attempt 2: 4 executor pairs all success, reducer success.
    # Sibling order within an attempt is non-deterministic; the test asserts
    # on event-type multisets rather than positional equality.

    def planner_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(submit_planner_outcome, _three_plus_one_plan())

    def executor_actions(self, ctx: ScenarioContext) -> Sequence[str]:
        instruction = ctx.instruction or ctx.prompt or ""
        if _FAIL_TAG in instruction and ctx.attempt.attempt_sequence_no == 1:
            tag = _field(instruction, "tag") or "quiescence"
            return (f"fail:Intentional generator failure on attempt 1 ({tag}).",)
        return ("preflight",)

    def reducer_response(self, ctx: ScenarioContext) -> ToolCallSpec:  # noqa: ARG002
        return ToolCallSpec(
            submit_reducer_outcome,
            {
                "status": "success",
                "outcome": (
                    "All four preflight nodes passed on the retry attempt; "
                    "quiescence behaviour exercised on attempt 1."
                ),
            },
        )


__all__ = ["GeneratorFailureQuiescence"]
